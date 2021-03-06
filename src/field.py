from numpy import *
import numpy as np # to help in transition
from safe_pylab import *
import matplotlib.pyplot as plt # transitioning...

import glob,types

from numpy.random import random
from numpy import ma
from numpy.linalg import norm

import tempfile
import pdb

from scipy import interpolate
from scipy.stats import nanmean
from scipy import signal
from scipy.ndimage import map_coordinates
from array_append import array_append

from scipy.interpolate import RectBivariateSpline

try:
    # from matplotlib import delaunay # deprecated
    from matplotlib import tri
except ImportError:
    print "delaunay not available."


try:
    from matplotlib import cm
except ImportError:
    cm = None
    
from safe_rtree import Rtree
xxyy = array([0,0,1,1])

from linestring_utils import upsample_linearring

    
import cPickle,subprocess,threading

try:
    from osgeo import gdal,osr,ogr
except ImportError:
    import gdal,osr,ogr
    
from shapely import geometry, wkb
try:
    from shapely.prepared import prep
except ImportError:
    prep = none
    
import os.path


numpy_type_to_gdal = {int8:gdal.GDT_Byte,
                      float32:gdal.GDT_Float32,
                      float64:gdal.GDT_Float64,
                      int16:gdal.GDT_Int16,
                      int32:gdal.GDT_Int32,
                      int:gdal.GDT_Int32,
                      uint16:gdal.GDT_UInt16,
                      uint32:gdal.GDT_UInt32}



#  # try to create an easier way to handle non-uniform meshes.  In particular
#  # it would be nice to be able to something like:
#  
#  foo = field.gdal_source('foo.asc') # grid is lat/lon
#  
#  foo_utm = foo.transform_to('EPSG:26910')
#  
#  bar = field.xyz_source('bar.xyz') # point data
#  
#  # this uses the foo_utm grid, adds values interpolated from bar, and any
#  # points where bar cannot interpolate are assigned nan.
#  foo_bar = foo_utm.add(bar,keep='valid')


class Field(object):
    """ Superclass for spatial fields
    """
    def __init__(self,projection=None):
        self._projection = projection
        
    def reproject(self,from_projection=None,to_projection=None):
        """ Reproject to a new coordinate system.
        If the input is structured, this will create a curvilinear
        grid, otherwise it creates an XYZ field.
        """

        xform = self.make_xform(from_projection,to_projection)
        new_field = self.apply_xform(xform)
        
        new_field._projection = to_projection
        return new_field
        
    def make_xform(self,from_projection,to_projection):
        if from_projection is None:
            from_projection = self.projection()
            if from_projection is None:
                raise Exception,"No source projection can be determined"
        
        src_srs = osr.SpatialReference()
        src_srs.SetFromUserInput(from_projection)

        dest_srs = osr.SpatialReference()
        dest_srs.SetFromUserInput(to_projection)

        xform = osr.CoordinateTransformation(src_srs,dest_srs)

        return xform

    def xyz(self):
        raise Exception,"Not implemented"
    def crop(self,rect):
        raise Exception,"Not implemented"
    def projection(self):
        return self._projection
    def bounds(self):
        raise Exception,"Not Implemented"

    def bounds_in_cs(self,cs):
        b = self.bounds()

        xform = self.make_xform(self.projection(),cs)

        corners = [ [b[0],b[2]],
                    [b[0],b[3]],
                    [b[1],b[2]],
                    [b[1],b[3]] ]

        new_corners = array( [xform.TransformPoint(c[0],c[1])[:2] for c in corners] )

        xmin = new_corners[:,0].min()
        xmax = new_corners[:,0].max()
        ymin = new_corners[:,1].min()
        ymax = new_corners[:,1].max()

        return [xmin,xmax,ymin,ymax]
    def quantize_space(self,quant):
        self.X = round_(self.X)

    def envelope(self,eps=1e-4):
        """ Return a rectangular shapely geometry the is the bounding box of
        this field.
        """
        b = self.bounds()

        return geometry.Polygon( [ [b[0]-eps,b[2]-eps],
                                   [b[1]+eps,b[2]-eps],
                                   [b[1]+eps,b[3]+eps],
                                   [b[0]-eps,b[3]+eps],
                                   [b[0]-eps,b[2]-eps] ])

    ## Some methods taken from density_field, which Field will soon supplant
    def value(self,X):
        """ in density_field this was called 'scale' - evaluates the field
        at the given point or vector of points.  Some subclasses can be configured
        to interpolate in various ways, but by default should do something reasonable
        """
        raise Exception,"not implemented"
        # X = array(X)
        # return self.constant * ones(X.shape[:-1])

    def value_on_edge(self,e,samples=5):
        """ Return the value averaged along an edge - the generic implementation
        just takes 5 samples evenly spaced along the line, using value()
        """
        x=linspace(e[0,0],e[1,0],samples)
        y=linspace(e[0,1],e[1,1],samples)
        X = array([x,y]).transpose()
        return nanmean(self.value(X))

    def __call__(self,X):
        return self.value(X)

    def __mul__(self,other):
        return BinopField(self,multiply,other)

    def __rmul__(self,other):
        return BinopField(other,multiply,self)

    def __add__(self,other):
        return BinopField(self,add,other)
    
    def __sub__(self,other):
        return BinopField(self,subtract,other)

    def to_grid(self,nx=None,ny=None,interp='nn',bounds=None,dx=None,dy=None,valuator='value'):
        """ bounds is a 2x2 [[minx,miny],[maxx,maxy]] array, and is *required* for BlenderFields
        bounds can also be a 4-element sequence, [xmin,xmax,ymin,ymax], for compatibility with
        matplotlib axis(), and Paving.default_clip.
        
        specify *one* of:
          nx,ny: specify number of samples in each dimension
          dx,dy: specify resolution in each dimension
        """
        if bounds is None:
            xmin,xmax,ymin,ymax = self.bounds()
        else:
            if len(bounds) == 2:
                xmin,ymin = bounds[0]
                xmax,ymax = bounds[1]
            else:
                xmin,xmax,ymin,ymax = bounds

        if nx is not None:
            x = linspace( xmin,xmax, nx )
            y = linspace( ymin,ymax, ny )
        elif dx is not None:
            x = arange(xmin,xmax,dx)
            y = arange(ymin,ymax,dy)
        else:
            raise Exception,"Either nx/ny or dx/dy must be specified"

        xx,yy = meshgrid(x,y)

        X = concatenate( (xx[...,newaxis], yy[...,newaxis]), axis=2)

        if valuator=='value':
            newF = self.value(X)
        else:
            valuator == getattr(self,valuator)
            newF = valuator(X)
            
        return SimpleGrid(extents=[xmin,xmax,ymin,ymax],
                          F=newF,projection=self.projection())

    
        
        
                   


# Different internal representations:
#   SimpleGrid - constant dx, dy, data just stored in array.

class XYZField(Field):
    def __init__(self,X,F,projection=None,from_file=None):
        """ X: Nx2 array of x,y locations
            F: N   array of values
        """
        Field.__init__(self,projection=projection)
        self.X = X
        self.F = F
        self.index = None
        self.from_file = from_file

        self.init_listeners()
        
        
    def plot(self,**kwargs):
        # this is going to be slow...
        def_args = {'c':self.F,
                    'antialiased':False,
                    'marker':'s',
                    'lod':True,
                    'lw':0}
        def_args.update(kwargs)
        scatter( self.X[:,0].ravel(),
                       self.X[:,1].ravel(),
                       **def_args)

    def bounds(self):
        if self.X.shape[0] == 0:
            return None
        
        xmin = self.X[:,0].min()
        xmax = self.X[:,0].max()
        ymin = self.X[:,1].min()
        ymax = self.X[:,1].max()

        return (xmin,xmax,ymin,ymax)

    def apply_xform(self,xform):
        new_X = self.X.copy()

        print "Transforming points"
        for i in range(len(self.F)):
            if i % 10000 == 0:
                print "%.2f%%"%( (100.0*i) / len(self.F))
                
            new_X[i] = xform.TransformPoint(*self.X[i])[:2]
        print "Done transforming points"

        # projection should get overwritten by the caller
        return XYZField( new_X, self.F, projection='reprojected')

    # an XYZ Field of our voronoi points
    _tri = None
    def tri(self):
        if self._tri is None:
            self._tri = tri.Triangulation(self.X[:,0],
                                          self.X[:,1])
        return self._tri

    def plot_tri(self,**kwargs):
        if 0: # deprecated matplotlib.delaunay stuff
            import plot_utils
            plot_utils.plot_tri(self.tri(),**kwargs)
        else:
            return plt.triplot(self.tri(),**kwargs)
    
    _nn_interper = None
    def nn_interper(self):
        if self._nn_interper is None:
            self._nn_interper = self.tri().nn_interpolator(self.F)
        return self._nn_interper
    _lin_interper = None
    def lin_interper(self):
        if self._lin_interper is None:
            if 0:
                # this works with the deprecated matplotlib.delaunay code
                self._lin_interper = self.tri().linear_interpolator(self.F)
            else:
                lti=tri.LinearTriInterpolator(self.tri(),
                                              self.F)
                self._lin_interper = lti
        return self._lin_interper
    
    #_voronoi = None
    default_interpolation='naturalneighbor'
    def interpolate(self,X,interpolation=None):
        if interpolation is None:
            interpolation=self.default_interpolation
        # X should be a (N,2) vectors

        newF = zeros( X.shape[0], float64 )

        if interpolation=='nearest':
            for i in range(len(X)):
                if i % 10000 == 1:
                    print " %.2f%%"%( (100.0*i)/len(X) )

                if not self.index:
                    dsqr = ((self.X - X[i])**2).sum(axis=1)
                    j = argmin( dsqr )
                else:
                    j = self.nearest(X[i])

                newF[i] = self.F[j]
        elif interpolation=='naturalneighbor':
            newF = self.nn_interper()(X[:,0],X[:,1])
            # print "why aren't you using linear?!"
        elif interpolation=='linear':
            interper = self.lin_interper()
            if 0: # deprecated matplotlib.delaunay interface
                for i in range(len(X)):
                    if i>0 and i%10000==0:
                        print "%d/%d"%(i,len(X))
                    # remember, the slices are y, x
                    vals = interper[X[i,1]:X[i,1]:2j,X[i,0]:X[i,0]:2j]
                    newF[i] = vals[0,0]
            else:
                newF[:] = interper(X[:,0],X[:,1])
        #elif interpolation=='delaunay':
        #    if self._voronoi is None:
        #        self._voronoi = self.calc_voronoi()
        #
        #    for i in range(len(X)):
        #        # get a cell index...
        #        cell = self._voronoi.closest( X[i] )
        #        # then which of our vertices are in the cell:
        #        abc = self._voronoi.nodes(cell)
        #        # and the locations of those nodes, relative to us
        #        pnts = self.X[abc]
        #        # along the AB edge:
        #        AB = (pnts[1] - pnts[0])
        #        lenAB = sqrt( (AB*AB).sum() )
        #        ABunit = AB / lenAB
        #        AX = (X[i] - pnts[0])
        #        # project AX onto AB
        #        alongAX = (AX[0]*ABunit[0] + AX[1]*ABunit[1])
        #        beta = alongAX / lenAB
        #        alpha = 1-beta
        #        D = alpha*pnts[0] + beta*pnts[1]
        #        
        #        DC = pnts[2] - D
        #        lenDC = sqrt( (DC*DC).sum() )
        #        DCunit = DC / lenDC
        #        DX = X[i] - D
        #
        #        alongDC = DX[0]*DCunit[0] + DX[1]*DCunit[1]
        #        gamma = alongDC / lenDC
        #        alpha *= (1-gamma)
        #        beta  *= (1-gamma)
        #        
        #        # now linearly across the triangle:
        #        v = alpha*self.F[abc[0]] + \
        #            beta *self.F[abc[1]] + \
        #            gamma*self.F[abc[2]]
        #        newF[i] = v
        else:
            raise Exception,"Bad value for interpolation method %s"%interpolation
        return newF

    def build_index(self,index_type='rtree'):
        if index_type != 'rtree':
            print "Ignoring request for non-rtree index"
            
        if self.X.shape[0] > 0:
            # the easy way:
            # tuples = [(i,self.X[i,xxyy],None) for i in range(self.X.shape[0])]

            # but this way we get some feedback
            def gimme():
                i = gimme.i
                if i < self.X.shape[0]:
                    if i %10000 == 0 and i>0:
                        print "building index: %d  -  %.2f%%"%(i, 100.0 * i / self.X.shape[0] )
                    gimme.i = i+1
                    return (i,self.X[i,xxyy],None)
                else:
                    return None
            gimme.i = 0

            tuples = iter(gimme,None)

            # If we can build the index on disk, go for it...
            index_fname = self.index_fname()
            if index_fname is not None:
                # See if the index is as new as the .bin file
                index_exists = os.path.exists(index_fname+".dat")
                if index_exists:
                    index_mtime = os.stat(index_fname+".dat").st_mtime
                    bin_mtime = os.stat(self.from_file).st_mtime

                    if index_mtime < bin_mtime:
                        print "Index is too old"
                        os.unlink(index_fname+".dat")
                        os.unlink(index_fname+".idx")
                        index_exists = False

                if not index_exists:
                    print "trying to build on-disk index in %s"%index_fname
                    tmp_index = Rtree(index_fname,tuples,interleaved=False)
                    # weird, but we have to delete it to actually force the
                    # data to be written out
                    del tmp_index

                # Then open it again for read
                self.index = Rtree(index_fname,interleaved=False)
            else:
                #print "just building Rtree index in memory"
                self.index = Rtree(tuples,interleaved=False)
        else:
            self.index = Rtree(interleaved=False)

    def index_fname(self):
        """ Returns either the filename to hand off for storing an index
        on disk, or None if an index is not desired
        """
        if self.from_file is not None:
            return self.from_file + '.index'
        else:
            return None
        
    def within_r(self,p,r):
        if self.index:
            # first query a rectangle
            rect = array( [p[0]-r,p[0]+r,p[1]-r,p[1]+r] )

            subset = self.index.intersection( rect )
            if isinstance(subset, types.GeneratorType):
                subset = list(subset)
            subset = array( subset )

            if len(subset) > 0:
                dsqr = ((self.X[subset]-p)**2).sum(axis=1)
                subset = subset[ dsqr<=r**2 ]

            return subset
        else:
            # print "bad - no index"
            dsqr = ((self.X-p)**2).sum(axis=1)
            return where(dsqr<=r**2)[0]

    def inv_dist_interp(self,p,
                        min_radius=None,min_n_closest=None,
                        clip_min=-inf,clip_max=inf,
                        default=None):
        """ inverse-distance weighted interpolation
        This is a bit funky because it tries to be smart about interpolation
        both in dense and sparse areas.

        min_radius: sample from at least this radius around p
        min_n_closest: sample from at least this many points

        """
        if min_radius is None and min_n_closest is None:
            raise Exception,"Must specify one of r (radius) or n_closest"
        
        r = min_radius
        
        if r:
            nearby = self.within_r(p,r)
                
            # have we satisfied the criteria?  if a radius was specified
            if min_n_closest is not None and len(nearby) < min_n_closest:
                # fall back to nearest
                nearby = self.nearest(p,min_n_closest)
        else:
            # this is slow when we have no starting radius
            nearby = self.nearest(p,min_n_closest)

        dists = sqrt( ((p-self.X[nearby])**2).sum(axis=1) )

        # may have to trim back some of the extras:
        if r is not None and r > min_radius:
            good = argsort(dists)[:min_n_closest]
            nearby = nearby[good]
            dists = dists[good]

        if min_radius is None:
            # hrrmph.  arbitrary...
            min_radius = dists.mean()

        dists[ dists < 0.01*min_radius ] = 0.01*min_radius
        
        weights = 1.0/dists

        vals = self.F[nearby]
        vals = clip(vals,clip_min,clip_max)

        val = (vals * weights).sum() / weights.sum()
        return val

    def nearest(self,p,count=1):
        # print "  Field::nearest(p=%s,count=%d)"%(p,count)
        
        if self.index:
            hits = self.index.nearest( p[xxyy], count )
            # deal with API change in RTree
            if isinstance( hits, types.GeneratorType):
                hits = [hits.next() for i in range(count)]

            if count == 1:
                return hits[0]
            else:
                return array(hits)
        else:
            # straight up, it takes 50ms per query for a small
            # number of points
            dsqr = ((self.X - p)**2).sum(axis=1)

            if count == 1:
                j = argmin( dsqr )
                return j
            else:
                js = argsort( dsqr )
                return js[:count]

    def rectify(self,dx=None,dy=None):
        """ Convert XYZ back to SimpleGrid.  Assumes that the data fall on a regular
        grid.  if dx and dy are None, automatically find the grid spacing/extents.
        """
        max_dimension = 10000.
        
        # Try to figure out a rectilinear grid that fits the data:
        xmin,xmax,ymin,ymax = self.bounds()

        # establish lower bound on delta x:
        if dx is None:
            min_deltax = (xmax - xmin) / max_dimension
            xoffsets = self.X[:,0] - xmin
            dx = xoffsets[xoffsets>min_deltax].min()

        if dy is None:
            min_deltay = (ymax - ymin) / max_dimension
            yoffsets = self.X[:,1] - ymin
            dy = yoffsets[yoffsets>min_deltay].min()

        print "Found dx=%g  dy=%g"%(dx,dy)

        nrows = 1 + int( 0.49 + (ymax - ymin) / dy )
        ncols = 1 + int( 0.49 + (xmax - xmin) / dx )

        # recalculate dx to be accurate over the whole range:
        dx = (xmax - xmin) / (ncols-1)
        dy = (ymax - ymin) / (nrows-1)
        delta = array([dx,dy])
        
        newF = nan*ones( (nrows,ncols), float64 )

        new_indices = (self.X - array([xmin,ymin])) / delta + 0.49
        new_indices = new_indices.astype(int32)
        new_indices = new_indices[:,::-1]

        newF[new_indices[:,0],new_indices[:,1]] = self.F

        return SimpleGrid(extents=[xmin,xmax,ymin,ymax],
                          F=newF,projection=self.projection())

    def to_grid(self,nx=2000,ny=2000,interp='nn',bounds=None,dx=None,dy=None):
        """ use the delaunay based griddata() to interpolate this field onto
        a rectilinear grid.  In theory interp='linear' would give bilinear
        interpolation, but it tends to complain about grid spacing, so best to stick
        with the default 'nn' which gives natural neighbor interpolation and is willing
        to accept a wider variety of grids

        Here we use a specialized implementation that passes the extent/stride array
        to interper, since lin_interper requires this.
        """
        if bounds is None:
            xmin,xmax,ymin,ymax = self.bounds()
        else:
            if len(bounds) == 4:
                xmin,xmax,ymin,ymax = bounds
            else:
                xmin,ymin = bounds[0]
                xmax,ymax = bounds[1]

        if dx is not None: # Takes precedence of nx/ny
            # round xmin/ymin to be an even multiple of dx/dy
            xmin = xmin - (xmin%dx)
            ymin = ymin - (ymin%dy)
            
            nx = int( (xmax-xmin)/dx )
            ny = int( (ymax-ymin)/dy )
            xmax = xmin + nx*dx
            ymax = ymin + ny*dy
            
        # hopefully this is more compatible between versions, also exposes more of what's
        # going on
        if interp == 'nn':
            interper = self.nn_interper()
        elif interp=='linear':
            interper = self.lin_interper()
        if 0:
            # this worked with matplotlib.delaunay
            griddedF = interper[ymin:ymax:ny*1j,xmin:xmax:nx*1j]
        else: # for newer matplotlib
            X,Y = np.meshgrid(  linspace(xmin,xmax,nx),linspace(ymin,ymax,ny) )
            griddedF = interper(X,Y)

        return SimpleGrid(extents=[xmin,xmax,ymin,ymax],F=griddedF)


    def crop(self,rect):
        xmin,xmax,ymin,ymax = rect

        good = (self.X[:,0] >= xmin ) & (self.X[:,0] <= xmax ) & (self.X[:,1] >= ymin) & (self.X[:,1]<=ymax)

        newX = self.X[good,:]
        newF = self.F[good]
        
        return XYZField(newX,newF, projection = self.projection() )
    def write_text(self,fname,sep=' '):
        fp = file(fname,'wt')

        for i in range(len(self.F)):
            fp.write( "%f%s%f%s%f\n"%(self.X[i,0],sep,
                                      self.X[i,1],sep,
                                      self.F[i] ) )
        fp.close()

    def intersect(self,other,op,radius=0.1):
        """ Create new pointset that has points that are in both fields, and combine
        the values with the given operator op(a,b)
        """
        my_points = []
        new_F = []
        
        if not self.index:
            self.build_index()

        for i in range(len(other.F)):
            if i % 10000 == 0:
                print "%.2f%%"%(100.0*i/len(other.F))
                
            p = self.within_r( other.X[i], radius )
            if len(p) > 0:
                # fudge it and take the first one...
                my_points.append(p[0])
                new_F.append( op(self.F[p[0]],other.F[i] ) )
        my_points = array(my_points)
        new_F = array(new_F)
        
        new_X = self.X[ my_points ]

        return XYZField( new_X, new_F )
    
    def decimate(self,factor):
        chooser = random( self.F.shape ) < 1.0/factor

        return XYZField( self.X[chooser,:], self.F[chooser], projection = self.projection() )

    def clip_to_polygon(self,poly):
        if not self.index:
            self.build_index()

        if prep:
            chooser = zeros(len(self.F),bool8)
            
            prep_poly = prep(poly)
            for i in xrange(len(self.F)):
                chooser[i] = prep_poly.contains( geometry.Point(self.X[i]) )
        else:
            # this only works with the stree implementation.
            # but stree is no longer supported... FIX
            chooser = self.index.inside_polygon(poly)
            
        if len(chooser) == 0:
            print "Clip to polygon got no points!"
            print "Returning empty field"
            return XYZField( zeros((0,2),float64), zeros( (0,1), float64) )
        else:
            return XYZField( self.X[chooser,:], self.F[chooser] )

    def cs2cs(self,
              src="+proj=utm +zone=10 +datum=NAD27 +nadgrids=conus",
              dst="+proj=utm +zone=10 +datum=NAD83"):
        """  In place modification of coordinate system.  Defaults to UTM NAD27 -> UTM NAD83
        """
        cmd = "cs2cs -f '%%f' %s +to %s"%(src,dst)

        proc = subprocess.Popen(cmd,shell=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE)

        pnts = []
        def reader():
            while 1:
                line = proc.stdout.readline()
                if line == '':
                    break
                pnts.append(map(float,line.split()[:2]))

        thr = threading.Thread(target = reader)
        thr.start()

        point_count = len(self.F)
        for i in range(point_count):
            if i % 10000 == 0:
                print "%.2f%%"%( (100.0*i)/point_count )
            proc.stdin.write("%.2f %.2f\n"%(self.X[i,0], self.X[i,1]) )
        proc.stdin.close()

        print "Finished writing"
        thr.join()

        pnts = array(pnts)

        if pnts.shape != self.X.shape:
            raise Exception('Size of converted points is %s, not %s'%( pnts.shape, self.X.shape ) )
        self.X = pnts


    def write(self,fname):
        fp = file(fname,'wb')

        cPickle.dump( (self.X,self.F), fp, -1)
        fp.close()

    def to_xyz(self):
        # should this be self, or a copy of self???
        return self


    @staticmethod 
    def read_shp(shp_name,value_field='value'):
        ods = ogr.Open(shp_name)

        X = []
        F = []

        layer = ods.GetLayer(0)

        while 1:
            feat = layer.GetNextFeature()

            if feat is None:
                break

            F.append( feat.GetField(value_field) )

            geo = feat.GetGeometryRef()
            
            X.append( geo.GetPoint_2D() )
        X = array( X )
        F = array( F )
        return XYZField(X=X,F=F,from_file=shp_name)

            
    def write_shp(self,shp_name,value_field='value'):
        drv = ogr.GetDriverByName('ESRI Shapefile')
        
        ### open the output shapefile
        if os.path.exists(shp_name) and shp_name.find('.shp')>=0:
            print "removing ",shp_name
            os.unlink(shp_name)

        ods = drv.CreateDataSource(shp_name)
        srs = osr.SpatialReference()
        if self.projection():
            srs.SetFromUserInput(self.projection())
        else:
            srs.SetFromUserInput('EPSG:26910')

        layer_name = os.path.splitext( os.path.basename(shp_name) )[0]
        
        ### Create the layer
        olayer = ods.CreateLayer(layer_name,
                                 srs=srs,
                                 geom_type=ogr.wkbPoint)
        
        olayer.CreateField(ogr.FieldDefn('id',ogr.OFTInteger))
        olayer.CreateField(ogr.FieldDefn(value_field,ogr.OFTReal))
        
        fdef = olayer.GetLayerDefn()
        
        ### Iterate over depth data
        for i in range(len(self.X)):
            x,y = self.X[i]

            wkt = geometry.Point(x,y).wkt

            new_feat_geom = ogr.CreateGeometryFromWkt( wkt )
            feat = ogr.Feature(fdef)
            feat.SetGeometryDirectly(new_feat_geom)
            feat.SetField('id',i)
            feat.SetField(value_field,self.F[i])

            olayer.CreateFeature(feat)

        olayer.SyncToDisk()

        ### Create spatial index:
        ods.ExecuteSQL("create spatial index on %s"%layer_name)

        
    @staticmethod
    def read(fname):
        fp = file(fname,'rb')
        X,F = cPickle.load( fp )
        fp.close()
        return XYZField(X=X,F=F,from_file=fname)

    @staticmethod
    def merge(all_sources):
        all_X = concatenate( [s.X for s in all_sources] )
        all_F = concatenate( [s.F for s in all_sources] )

        return XYZField(all_X,all_F,projection=all_sources[0].projection())


    ## Editing API for use with GUI editor
    def move_point(self,i,pnt):
        self.X[i] = pnt
        
        if self.index:
            old_coords = self.X[i,xxyy]
            new_coords = pnt[xxyy]

            self.index.delete(i, old_coords )
            self.index.insert(i, new_coords )
        self.updated_point(i)

    def add_point(self,pnt,value):
        """ Insert a new point into the field, clearing any invalidated data
        and returning the index of the new point
        """
        i = len(self.X)

        self.X = array_append(self.X,pnt)
        self.F = array_append(self.F,value)

        self._tri = None
        self._nn_interper = None
        self._lin_interper = None
        
        if self.index is not None:
            print "Adding new point %d to index at "%i,self.X[i]
            self.index.insert(i, self.X[i,xxyy] )

        self.created_point(i)
        return i

    def delete_point(self,i):
        if self.index is not None:
            coords = self.X[i,xxyy]
            self.index.delete(i, coords )
            
        self.X[i,0] = nan
        self.F[i] = nan
        self.deleted_point(i)

    
    # subscriber interface for updates:
    listener_count = 0
    def init_listeners(self):
        self._update_point_listeners = {}
        self._create_point_listeners = {}
        self._delete_point_listeners = {}
    
    def listen(self,event,cb):
        cb_id = self.listener_count
        if event == 'update_point':
            self._update_point_listeners[cb_id] = cb
        elif event == 'create_point':
            self._create_point_listeners[cb_id] = cb
        elif event == 'delete_point':
            self._delete_point_listeners[cb_id] = cb
        else:
            raise Exception,"unknown event %s"%event
            
        self.listener_count += 1
        return cb_id
    def updated_point(self,i):
        for cb in self._update_point_listeners.values():
            cb(i)
    def created_point(self,i):
        for cb in self._create_point_listeners.values():
            cb(i)
    def deleted_point(self,i):
        for cb in self._delete_point_listeners.values():
            cb(i)

    ## Methods taken from XYZDensityField
    def value(self,X):
        """ X must be shaped (...,2)
        """
        
        X = array(X)
        orig_shape = X.shape

        X = reshape(X,(-1,2))

        newF = self.interpolate(X)
        
        newF = reshape( newF, orig_shape[:-1])
        if newF.ndim == 0:
            return float(newF)
        else:
            return newF

    def plot_on_boundary(self,bdry):
        # bdry is an array of vertices (presumbly on the boundary)
        l = zeros( len(bdry), float64 )

        ax = gca()
        for i in range(len(bdry)):
            l[i] = self.value( bdry[i] )

            cir = Circle( bdry[i], radius=l[i])
            ax.add_patch(cir)


has_apollonius=False
try:
    import CGAL
    # And does it have Apollonius graph bindings?
    cgal_bindings = None
    try:
        from CGAL import Point_2,Site_2
        import CGAL.Apollonius_Graph_2 as Apollonius_Graph_2
        cgal_bindings = 'old'
    except ImportError:
        pass
    if cgal_bindings is None:
        # let it propagate out
        from CGAL.CGAL_Kernel import Point_2
        from CGAL.CGAL_Apollonius_Graph_2 import Apollonius_Graph_2,Site_2
        # print "Has new bindings"
        cgal_bindings = 'new'
    
    has_apollonius=True
    class ApolloniusField(XYZField):
        """ Takes a set of vertices and the allowed scale at each, and
        extrapolates across the plane based on a uniform telescoping rate
        """

        # Trying to optimize some -
        #   it segfault under the conditions:
        #      locality on insert
        #      locality on query
        #      redundant_factor = 0.9
        #      quantize=True/False

        # But it's okay if redundant factor is None

        # These are disabled while debugging the hangs on CGAL 4.2
        # with new bindings
        # enable using the last insert as a clue for the next insert
        locality_on_insert = False # True
        # enable using the last query as a clue for the next query
        locality_on_query = False # True
        quantize=False
        
        def __init__(self,X,F,r=1.1,redundant_factor=None):
            """
            redundant_factor: if a point being inserted has a scale which is larger than the redundant_factor
            times the existing scale at its location, then don't insert it.  So typically it would be something
            like 0.95, which says that if the existing scale at X is 100, and this point has a scale of 96, then
            we don't insert.
            """
            XYZField.__init__(self,X,F)
            self.r = r
            self.redundant_factor = redundant_factor
            self.construct_apollonius_graph()

        # Pickle support -
        def __getstate__(self):
            """ the CGAL.ApolloniusGraph can't be pickled - have to recreate it
            """
            d = self.__dict__.copy()
            d['ag'] = 'recreate'
            d['last_inserted'] = None
            d['last_query_vertex'] = None
            return d
        def __setstate__(self,d):
            self.__dict__.update(d)
            self.construct_apollonius_graph()

        def construct_apollonius_graph(self,quantize=False):
            """
            quantize: coordinates will be truncated to integers.  Not sure why this is relevant -
            might make it faster or more stable??  pretty sure that repeated coordinates will
            keep only the tightest constraint
            """
            self.quantize = quantize
            if len(self.X) > 0:
                self.offset = self.X.mean(axis=0)
            else:
                self.offset = zeros(2)

            print "Constructing Apollonius Graph.  quantize=%s"%quantize
            self.ag = ag = Apollonius_Graph_2()
            self.last_inserted = None

            # if self.redundant_factor is not None:
            self.redundant = zeros(len(self.X),bool8)
                
            for i in range(len(self.X)):
                if i % 100 == 0:
                    print " %8i / %8i"%(i,len(self.X))
                self.redundant[i] = not self.insert(self.X[i],self.F[i])
            print "Done!"

        def insert(self,xy,f):
            """ directly insert a point into the Apollonius graph structure
            note that this may be used to incrementally construct the graph,
            if the caller doesn't care about the accounting related to the
            field -
            returns False if redundant checks are enabled and the point was
            deemed redundant.
            """
            x,y = xy - self.offset
            # This had been just -self.F[i], but I think that was wrong.
            w = -(f / (self.r-1.0) )
            if self.quantize:
                x = int(x)
                y = int(y)

            pnt = Point_2(x,y)
            ##
            if self.redundant_factor is not None:
                if self.ag.number_of_vertices() > 0:
                    existing_scale = self.value_at_point(pnt)
                    if self.redundant_factor * existing_scale < f:
                        return False
            ## 
            if self.locality_on_insert and self.last_inserted is not None:
                # generally the incoming data have some locality - this should speed things
                # up.
                try:
                    self.last_inserted = self.ag.insert(Site_2( pnt, w),self.last_inserted)
                except Exception: # no direct access to the real type, ArgumentError
                    print "CGAL doesn't have locality aware bindings.  This might be slower"
                    self.locality_on_insert=False
                    self.last_inserted = self.ag.insert(Site_2( pnt, w))
            else:
                s = Site_2(pnt,w)
                print "AG::insert: %f,%f,%f"%(s.point().x(),s.point().y(),s.weight())
                #self.last_inserted = self.ag.insert(s)
                # try avoiding saving the result
                self.ag.insert(s)
                # retrieve it to see if it really got inserted like we think
                v = self.ag.nearest_neighbor(pnt)
                s = v.site()
                print "            %f,%f,%f"%(s.point().x(),s.point().y(),s.weight())
            # it seems to crash if queries are allowed to retain this vertex handle -
            # probably the insertion can invalidate it
            self.last_query_vertex = None
            return True

        last_query_vertex = None
        def value_at_point(self,pnt):
            """ Like interpolate, but takes a CGAL point instead.  really just for the
            skip_redundant option, and called inside interpolate()
            """
            if self.ag.number_of_vertices() == 0:
                return nan
            
            if self.locality_on_query and self.last_query_vertex is not None:
                # exploit query locality
                try:
                    v = self.ag.nearest_neighbor(pnt,self.last_query_vertex)
                except Exception: # no direct access to the real type, ArgumentError
                    print "CGAL doesn't have locality aware query bindings.  May be slower."
                    self.locality_on_query = False
                    v = self.ag.nearest_neighbor(pnt)
            else:
                v = self.ag.nearest_neighbor(pnt)
            self.last_query_vertex = v
            site = v.site()
            dist = sqrt( (pnt.x() - site.point().x())**2 +
                         (pnt.y() - site.point().y())**2   )
            # before this didn't have the factor dividing site.weight()
            f = -( site.weight() * (self.r-1.0) ) + dist*(self.r-1.0)
            return f

        def interpolate(self,X):
            newF = zeros( X.shape[0], float64 )

            for i in range(len(X)):
                x,y = X[i] - self.offset
                # remember, the slices are y, x
                p = Point_2(x,y)
                newF[i] = self.value_at_point(p)

            return newF

        #def to_grid(self,nx=2000,ny=2000,interp='apollonius',bounds=None):
        def to_grid(self,nx=None,ny=None,interp='apollonius',bounds=None,dx=None,dy=None):
            if interp!='apollonius':
                print "NOTICE: Apollonius graph was asked to_grid using '%s'"%interp
                return XYZField.to_grid(self,nx=nx,ny=ny,interp=interp,bounds=bounds,dx=dx,dy=dy)

            #-- copied from XYZField - should abstract this out
            if bounds is None:
                xmin,xmax,ymin,ymax = self.bounds()
            else:
                if len(bounds) == 4:
                    xmin,xmax,ymin,ymax = bounds
                else:
                    xmin,ymin = bounds[0]
                    xmax,ymax = bounds[1]

            if dx is not None: # Takes precedence of nx/ny
                # round xmin/ymin to be an even multiple of dx/dy
                xmin = xmin - (xmin%dx)
                ymin = ymin - (ymin%dy)

                nx = int( (xmax-xmin)/dx )
                ny = int( (ymax-ymin)/dy )
                xmax = xmin + nx*dx
                ymax = ymin + ny*dy
            #-- end copy

            extents=[xmin,xmax,ymin,ymax]
            x = linspace(xmin,xmax,nx)
            y = linspace(ymin,ymax,ny)

            griddedF = zeros( (len(y),len(x)), float64 )

            for xi in range(len(x)):
                for yi in range(len(y)):
                    griddedF[yi,xi] = self( [x[xi],y[yi]] )

            return SimpleGrid(extents,griddedF)

        @staticmethod 
        def read_shps(shp_names,value_field='value',r=1.1,redundant_factor=None):
            """ Read points or lines from a list of shapefiles, and construct
            an apollonius graph from the combined set of features.  Lines will be
            downsampled at the scale of the line.
            """
            X = []
            F = []
            edges = []

            for shp_name in shp_names:
                print "Reading %s"%shp_name

                ods = ogr.Open(shp_name)

                layer = ods.GetLayer(0)

                while 1:
                    feat = layer.GetNextFeature()
                    if feat is None:
                        break

                    value = feat.GetField(value_field)
                    geo = wkb.loads(feat.GetGeometryRef().ExportToWkb())
                    coords = array(geo.coords)

                    if len(coords) > 1: # it's a line - upsample
                        # need to say closed_ring=0 so it doesn't try to interpolate between
                        # the very last point back to the first
                        coords = upsample_linearring(coords,value,closed_ring=0)
                    if all(coords[-1]==coords[0]):
                        coords = coords[:-1]

                    # remove duplicates:
                    mask = all(coords[0:-1,:] == coords[1:,:],axis=1)
                    if sum(mask)>0:
                        print "WARNING: removing duplicate points in shapefile"
                        print coords[mask]
                        coords = coords[~mask]

                    X.append( coords )
                    F.append( value*ones(len(coords)) )

            X = concatenate( X )
            F = concatenate( F )
            return ApolloniusField(X=X,F=F,r=r,redundant_factor=redundant_factor)
    
except ImportError:
    #print "CGAL unavailable."
    pass
except AttributeError:
    print "You have CGAL, but no Apollonius Graph bindings - auto-telescoping won't work"

class ConstrainedScaleField(XYZField):
    """ Like XYZField, but when new values are inserted makes sure that
    neighboring nodes are not too large.  If an inserted scale is too large
    it will be made smaller.  If a small scale is inserted, it's neighbors
    will be checked, and made smaller as necessary.  These changes are
    propagated to neighbors of neighbors, etc.

    As points are inserted, if a neighbor is far enough away, this will
    optionally insert new points along the edges connecting with that neighbor
    to limit the extent that the new point affects too large an area
    """
    r=1.1 # allow 10% growth per segment

    def check_all(self):
        t = self.tri()
        edges = t.edge_db

        Ls = sqrt( (t.x[edges[:,0]] - t.x[edges[:,1]])**2 +
                   (t.y[edges[:,0]] - t.y[edges[:,1]])**2  )
        dys = self.F[edges[:,0]] - self.F[edges[:,1]]
        
        slopes = abs(dys / Ls)

        if any(slopes > self.r-1.0):
            bad_edges = where(slopes > self.r-1.0)[0]
            
            print "Bad edges: "
            for e in bad_edges:
                a,b = edges[e]
                if self.F[a] > self.F[b]:
                    a,b = b,a
                
                L = sqrt( (t.x[a]-t.x[b])**2 + (t.y[a]-t.y[b])**2 )
                allowed = self.F[a] + L*(self.r - 1.0)
                print "%d:%f --[L=%g]-- %d:%f > %f"%(a,self.F[a],
                                                     L,
                                                     b,self.F[b],
                                                     allowed)
                print "  " + str( edges[e] )
            return False
        return True

    # how much smaller than the 'allowed' value to make nodes
    #  so if the telescope factor says that the node can be 10m,
    #  we'll actually update it to be 8.5m
    safety_factor = 0.85
    
    def add_point(self,pnt,value,allow_larger=False):
        accum = [] # accumulates a list of ( [x,y], scale ) tuples for limiter points
        
        # before adding, see if there is one already in there that's close by
        old_value = self(pnt)

        if old_value < 0:
            print "  count of negative values: ",sum(self.F < 0)
            print "  point in question: ",pnt
            print "  old_value",old_value
            fg = self.to_grid(1000,1000)
            fg.plot()
            global bad
            bad = self
            raise Exception,"Old value at new point is negative!"

        if not allow_larger and (value > old_value):
            print "Not adding this point, because it is actually larger than existing ones"
            return None

        ## ! Need to be careful about leaning to hard on old_value -
        #    the nearest neighbors interpolation doesn't guarantee the same value
        #    as linear interpolation between nodes ( I think ), so it's possible for
        #    things to look peachy keen from the nn interp but when comparing along edges
        #    it starts looking worse.

        ## STATUS
        #  I think the order of adding intermediate points needs to change.
        #  maybe we add the starting point, using it's old_value
        #  then look at its neighbors... confused...
        
        print "-----------Adding point: %s %g=>%g-----------"%(pnt,old_value,value)
        
        j = self.nearest(pnt)
        dist = sqrt( sum((self.X[j] - pnt)**2) )
        if dist < 0.5*value:
            i = j
            print "add_point redirected, b/c a nearby point already exists."
            # need an extra margin of safety here -
            #   we're updating a point that is dist away, and we need the scale
            #   right here to be value.  
            F_over_there = value - dist*(self.r-1.0)
            if F_over_there < self.F[i]:
                self.F[i] = self.safety_factor * F_over_there
                print "  updating value of %s to %f"%(i,self.F[i])
                self.check_scale(i,old_value = old_value)
        else:
            i = XYZField.add_point(self,pnt,value)
            print "  inserted %d with value %f"%(i,self.F[i])
            # these are the edges in which the new node participates
            self.check_scale(i,old_value=old_value)

        return i

    def check_scale(self,i,old_value=None):
        """
        old_value: if specified, on each edge, if the neighbor is far enough away, insert
        a new node along the edge at the scale that it would have been if we hadn't
        adjusted this node
        """
        # print "Check scale of %s"%i
        
        # First, make sure that we are not too large for any neighbors:
        t = self.tri()
        edges = where( t.edge_db == i )[0]
        for e in edges:
            a,b = t.edge_db[e]
            # enforce that a is the smaller of the two
            if self.F[a] > self.F[b]:
                a,b = b,a
            # this time around, we only care about places where i is the larger
            if a==i:
                continue
            
            L= sqrt( (t.x[a] - t.x[b])**2 + (t.y[a] - t.y[b])**2 )

            A = self.F[a]
            B = self.F[b]

            allowed = A + L*(self.r-1.0)

            if B > allowed:
                # print "Had to adjust down the requested scale of point"
                self.F[b] = self.safety_factor*allowed

        # Now we know that the new point is not too large for anyone - see if any of
        # it's neighbors are too small.
        
        to_visit = [ (i,old_value) ]
        to_add = []

        orig_i = i
        
        # used to be important for this to be breadth-first...
        # also, this whole thing is hack-ish.  
        while len(to_visit) > 0:
            i,old_value = to_visit.pop(0)

            t = self.tri()
            edges = where( t.edge_db == i )[0]

            for e in edges:
                a,b = t.edge_db[e]

                # Make b the one that is not i
                if b==i:
                    a,b = b,a

                # print "From a=%d visiting b=%d"%(a,b)

                # So we are checking on point b, having come from a, but
                # ultimately we just care whether b is valid w.r.t orig_i
                
                # print "Checking on edge ",a,b
                L = sqrt( (t.x[orig_i] - t.x[b])**2 + (t.y[orig_i] - t.y[b])**2 )
                La    = sqrt( (t.x[a] - t.x[b])**2 + (t.y[a] - t.y[b])**2 )
                # print "    Length is ",L

                ORIG = self.F[orig_i]
                A = self.F[a] # 
                B = self.F[b]
                # print "    Scales A(%d)=%g  B(%d)=%g"%(a,A,b,B)

                allowed = min( ORIG + L*(self.r-1.0),
                               A    + La*(self.r-1.0) )
                
                # print "    Allowed from %d or %d is B: %g"%(orig_i,a,allowed)
                
                if B > allowed:
                    self.F[b] = self.safety_factor * allowed # play it safe...
                    # print "  Updating B(%d) to allowed scale %f"%(b,self.F[b])
                    to_visit.append( (b,B) )
                # elif (B < 0.8*allowed) and (old_value is not None) and (A<0.8*old_value) and (L > 5*A):
                elif (B>A) and (old_value is not None) and (A<0.8*old_value) and (L>5*A):
                    # the neighbor was significantly smaller than the max allowed,
                    # so we should limit the influence of this new point.
                    #
                    # used to be a safety_factor*allowed here, now just allowed...
                    alpha = (old_value - A) / (old_value - A + allowed - B)
                    if alpha < 0.65:
                        # if the intersection is close to B, don't bother...
                        new_point = alpha*self.X[b] + (1-alpha)*self.X[a]
                        # another 0.99 just to be safe against rounding
                        # 
                        # New approach: use the distance to original point
                        newL = sqrt( (t.x[orig_i] - new_point[0])**2 + (t.y[orig_i] - new_point[1])**2 )

                        # constrained by valid value based on distance from starting point as well as
                        # the old value 
                        new_value = min(ORIG + 0.95*newL*(self.r-1.0), # allowed value 
                                        0.99*(alpha*B + (1-alpha)*old_value) ) # value along the old line

                        # print "INTERMEDIATE:"
                        # print "  old_value at A: %g"%old_value
                        # print "  new value at A: %g"%A
                        # print "  curr value at B: %g"%B
                        # print "  allowed at B: %g"%allowed
                        # print "  alpha from A: %g"%alpha
                        # print "  new value for interpolated point: %g"%new_value
                        # 
                        # print "Will add intermediate point %s = %g"%(new_point,new_value)
                        to_add.append( (new_point, new_value) )

                    
        print "Adding %d intermediate points"%len(to_add)
        for p,v in to_add:
            if v < 0:
                raise Exception,"Value of intermediate point is negative"
            i = self.add_point(p+0.01*v,v,allow_larger=1)
            # print "added intermediate point ",i


    def remove_invalid(self):
        """ Remove nodes that are too big for their delaunay neighbors
        """
        while 1:
            t = self.tri()
            edges = t.edge_db

            Ls = sqrt( (t.x[edges[:,0]] - t.x[edges[:,1]])**2 +
                       (t.y[edges[:,0]] - t.y[edges[:,1]])**2  )
            dys = self.F[edges[:,0]] - self.F[edges[:,1]]

            slopes = (dys / Ls)

            bad0 = slopes > self.r-1.0
            bad1 = (-slopes) > self.r-1.0

            bad_nodes = union1d( edges[bad0,0], edges[bad1,1] )
            if len(bad_nodes) == 0:
                break
            print "Removing %d of %d"%(len(bad_nodes),len(self.F))

            to_keep = ones(len(self.F),bool)
            to_keep[bad_nodes] = False

            self.F = self.F[to_keep]
            self.X = self.X[to_keep]

            self._tri = None
            self._nn_interper = None
            self._lin_interper = None
            self.index = None

        

            

class XYZText(XYZField):
    def __init__(self,fname,sep=None,projection=None):
        self.filename = fname
        fp = file(fname,'rt')

        data = array([map(float,line.split(sep)) for line in fp])
        fp.close()

        XYZField.__init__(self,data[:,:2],data[:,2],projection=projection)



## The rest of the density field stuff:
class ConstantField(Field):
    def __init__(self,c):
        self.c = c
        Field.__init__(self)
        
    def value(self,X):
        return self.c * ones(X.shape[:-1])
        

class BinopField(Field):
    """ Combine arbitrary fields with binary operators """
    def __init__(self,A,op,B):
        Field.__init__(self)
        self.A = A
        self.op = op
        self.B = B

    def __getstate__(self):
        d = self.__dict__.copy()
        d['op'] = self.op2str()
        return d
    def __setstate__(self,d):
        self.__dict__.update(d)
        self.op = self.str2op(self.op)

    # cross your fingers...
    def op2str(self):
        return self.op.__name__
    def str2op(self,s):
        return eval(s)
    
        
    def value(self,X):
        try: # if isinstance(self.A,Field):
            a = self.A.value(X)
        except:
            a = self.A
            
        try: # if isinstance(self.B,Field):
            b = self.B.value(X)
        except:
            b = self.B
            
        return self.op(a,b)



class Field3D(Field):
    pass

class ZLevelField(Field3D):
    """ One representation of a 3-D field.
    We have a set of XY points and a water column associated with each.
    Extrapolation pulls out the closest water column, and extends the lowest
    cell if necessary.
    """
    def __init__(self,X,Z,F):
        Field3D.__init__(self)

        self.X = X
        self.Z = Z
        self.F = ma.masked_invalid(F)

        # 2-D index:
        self.surf_field = XYZField(self.X,arange(len(self.X)))
        self.surf_field.build_index()

    def shift_z(self,delta_z):
        self.Z += delta_z
    
    def distance_along_transect(self):
        d = (diff(self.X,axis=0)**2).sum(axis=1)**0.5
        d = d.cumsum()
        d = concatenate( ([0],d) )
        return d
        
    def plot_transect(self):
        """ Plots the data in 2-D as if self.X is in order as a transect.
        The x axis will be distance between points.  NB: if the data are not
        organized along a curve, this plot will make no sense!
        """
        x = self.distance_along_transect()
        
        meshY,meshX = meshgrid(self.Z,x)
        all_x = meshX.ravel()
        all_y = meshY.ravel()
        all_g = transpose(self.F).ravel()

        if any(all_g.mask):
            valid = ~all_g.mask

            all_x = all_x[valid]
            all_y = all_y[valid]
            all_g = all_g[valid]
        scatter(all_x,all_y,60,all_g,linewidth=0)

    def plot_surface(self):
        scatter(self.X[:,0],self.X[:,1],60,self.F[0,:],linewidth=0)

    _cached = None # [(x,y),idxs]
    def extrapolate(self,x,y,z):
        pnt = array([x,y])
        if self._cached is not None  and (x,y) == self._cached[0]:
            idxs = self._cached[1]
        else:
            # find the horizontal index:
            count = 4
            idxs = self.surf_field.nearest(pnt,count)
            self._cached = [ (x,y), idxs]
        
        zi = searchsorted( self.Z,z)
        if zi >= len(self.Z):
            zi = len(self.Z) - 1

        vals = self.F[zi,idxs]
        
        weights = 1.0 / ( ((pnt - self.X[idxs] )**2).sum(axis=1)+0.0001)

        val = (vals*weights).sum() / weights.sum()
        return val
    

# from pysqlite2 import dbapi2 as sqlite
# 
# class XYZSpatiaLite(XYZField):
#     """ Use spatialite as a backend for storing an xyz field
#     """
#     def __init__(self,fname,src=None):
#         self.conn = sqlite.connect(fname)
#         self.conn.enable_load_extension(1)
#         self.curs = self.conn.cursor()
#         self.curs.execute("select load_extension('/usr/local/lib/libspatialite.so')")
# 
#         self.ensure_schema()
#         
#         if src:
#             self.load_from_field(src)
# 
#     schema = """
#     create table points (id, geom ..."""
#     def ensure_schema(self):
#         pass
#     
            

    

class QuadrilateralGrid(Field):
    """ Common code for grids that store data in a matrix
    """
    def to_xyz(self):
        xyz = self.xyz()

        good = ~isnan(xyz[:,2])

        return XYZField( xyz[good,:2], xyz[good,2], projection = self.projection() )

class CurvilinearGrid(QuadrilateralGrid):
    def __init__(self,X,F,projection=None):
        """ F: 2D matrix of data values
            X: [Frows,Fcols,2] matrix of grid locations [x,y]
            Assumes that the grid is reasonable (i.e. doesn't have intersecting lines
            between neighbors)
        """
        QuadrilateralGrid.__init__(self,projection=projection)
        self.X = X
        self.F = F

    def xyz(self):
        """ unravel to a linear sequence of points
        """
        xyz = zeros( (self.F.shape[0] * self.F.shape[1], 3), float64 )
        
        xyz[:,:2] = self.X.ravel()
        xyz[:,2] = self.F.ravel()

        return xyz

    def plot(self,**kwargs):
        import pylab 
        # this is going to be slow...
        self.scatter = pylab.scatter( self.X[:,:,0].ravel(),
                                      self.X[:,:,1].ravel(),
                                      c=self.F[:,:].ravel(),
                                      antialiased=False,marker='s',lod=True,
                                      lw=0,**kwargs )
        
    def apply_xform(self,xform):
        new_X = self.X.copy()

        print "Transforming points"
        for row in range(new_X.shape[0]):
            print "."
            for col in range(new_X.shape[1]):
                new_X[row,col,:] = xform.TransformPoint(*self.X[row,col])[:2]
        print "Done transforming points"
                        

        # projection should get overwritten by the caller
        return CurvilinearGrid(new_X,self.F,projection='reprojected')
                
    def bounds(self):
        xmin = self.X[:,:,0].min()
        xmax = self.X[:,:,0].max()
        ymin = self.X[:,:,1].min()
        ymax = self.X[:,:,1].max()

        return (xmin,xmax,ymin,ymax)

    # cross-grid arithmetic.  lots of room for optimization...

    def regrid(self,b,interpolation='nearest'):
        """ returns an F array corresponding to the field B interpolated
        onto our grid
        """

        X = self.X.reshape( (-1,2) )

        newF = b.interpolate(X,interpolation=interpolation)
        
        return newF.reshape( self.F.shape )
        
    def __sub__(self,b):
        if isinstance(b,CurvilinearGrid) and id(b.X) == id(self.X):
            print "Reusing this grid."
            Fb = self.F - b.F
        else:
            Fb = self.regrid( b )
            Fb = self.F - Fb

        return CurvilinearGrid(X=self.X, F= Fb, projection=self.projection() )
    
class SimpleGrid(QuadrilateralGrid):
    int_nan = -9999

    # Set to "linear" to have value() calls use linear interpolation
    default_interpolation = "nearest"
    
    def __init__(self,extents,F,projection=None):
        """ extents: minx, maxx, miny, maxy
            NB: these are node-centered values, so if you're reading in
            pixel-based data where the dimensions are given to pixel edges,
            be sure to add a half pixel.
        """
        self.extents = extents
        self.F = F

        QuadrilateralGrid.__init__(self,projection=projection)

        self.dx,self.dy = self.delta()

    def delta(self):
        return ( (self.extents[1] - self.extents[0]) / (self.F.shape[1]-1.0),
                 (self.extents[3] - self.extents[2]) / (self.F.shape[0]-1.0) )

    def contourf(self,*args,**kwargs):
        X,Y = self.XY()
        return contourf(X,Y,self.F,*args,**kwargs)
        
    def contour(self,*args,**kwargs):
        X,Y = self.XY()
        return contour(X,Y,self.F,*args,**kwargs)
            
    def plot(self,**kwargs):
        import pylab 
        dx,dy = self.delta()

        maskedF = ma.array(self.F,mask=isnan(self.F))

        if kwargs.has_key('ax'):
            kwargs = dict(kwargs)
            ax = kwargs['ax']
            del kwargs['ax']
            ims = ax.imshow
        else:
            ims = pylab.imshow
            
        return ims(maskedF,origin='lower',
                   extent=[self.extents[0]-0.5*dx, self.extents[1]+0.5*dx,
                           self.extents[2]-0.5*dy, self.extents[3]+0.5*dy],
                   **kwargs)

    def xy(self):
        x = linspace(self.extents[0],self.extents[1],self.F.shape[1])
        y = linspace(self.extents[2],self.extents[3],self.F.shape[0])
        return x,y
    
    def XY(self):
        X,Y = meshgrid(*self.xy())
        return X,Y

    def xyz(self):
        """ unravel to a linear sequence of points
        """
        X,Y = self.XY()

        xyz = zeros( (self.F.shape[0] * self.F.shape[1], 3), float64 )
        
        xyz[:,0] = X.ravel()
        xyz[:,1] = Y.ravel()
        xyz[:,2] = self.F.ravel()

        return xyz

    def to_xyz(self):
        """  The simple grid version is a bit smarter about missing values,
        and tries to avoid creating unnecessarily large intermediate arrays
        """
        x,y = self.xy()

        if hasattr(self.F,'mask') and self.F.mask is not False:
            self.F._data[ self.F.mask ] = nan
            self.F = self.F._data

        if self.F.dtype in (int16,int32):
            good = (self.F != self.int_nan)
        else:
            good = ~isnan(self.F)

        i,j = where(good)

        X = zeros( (len(i),2), float64 )
        X[:,0] = x[j]
        X[:,1] = y[i]
              

        return XYZField( X, self.F[good], projection = self.projection() )
 
        
    def to_curvilinear(self):
        X,Y = self.XY()
        
        XY = concatenate( ( X[:,:,newaxis], Y[:,:,newaxis]), axis=2)

        cgrid = CurvilinearGrid(XY,self.F)
        return cgrid
    
    def apply_xform(self,xform):
        # assume that the transform is not a simple scaling in x and y,
        # so we have to switch to a curvilinear grid.
        cgrid = self.to_curvilinear()

        return cgrid.apply_xform(xform)
        
    def crop(self,rect=None,indexes=None):
        dx,dy = self.delta()
        
        if rect is not None:
            xmin,xmax,ymin,ymax = rect


            min_col = max( floor( (xmin - self.extents[0]) / dx ), 0)
            max_col = min( ceil( (xmax - self.extents[0]) / dx ), self.F.shape[1]-1)

            min_row = max( floor( (ymin - self.extents[2]) / dy ), 0)
            max_row = min( ceil( (ymax - self.extents[2]) / dy ), self.F.shape[0]-1)

            print  min_row, max_row, min_col, max_col
            return self.crop(indexes=[min_row,max_row,min_col,max_col])
        elif indexes is not None:
            min_row,max_row,min_col,max_col = indexes
            newF = self.F[min_row:max_row+1, min_col:max_col+1]
            new_extents = [self.extents[0] + min_col*dx,
                           self.extents[0] + max_col*dx,
                           self.extents[2] + min_row*dy,
                           self.extents[2] + max_row*dy ]
            
            return SimpleGrid(extents = new_extents,
                              F = newF,
                              projection = self.projection() )
        else:
            raise Exception,"must specify one of rect [default] or indexes"

    def bounds(self):
        return array(self.extents)

    def interpolate(self,X,interpolation=None,fallback=True):
        """ interpolation can be nearest or linear
        """
        if interpolation is None:
            interpolation = self.default_interpolation
        
        xmin,xmax,ymin,ymax = self.bounds()
        dx,dy = self.delta()

        if interpolation == 'nearest':
            # 0.49 will give us the nearest cell center.
            # recently changed X[:,1] to X[...,1] - hopefully will accomodate
            # arbitrary shapes for X
            rows = (0.49 + (X[...,1] - ymin) / dy).astype(int32)
            cols = (0.49 + (X[...,0] - xmin) / dx).astype(int32)
            bad = (rows<0) | (rows>=self.F.shape[0]) | (cols<0) | (cols>=self.F.shape[1])
        elif interpolation == 'linear':
            # for linear, we choose the floor() of both
            row_alpha = ((X[...,1] - ymin) / dy)
            col_alpha = ((X[...,0] - xmin) / dx)

            rows = row_alpha.astype(int32)
            cols = col_alpha.astype(int32)

            row_alpha -= rows # [0,1]
            col_alpha -= cols # [0,1]

            # and we need one extra on the high end
            bad = (rows<0) | (rows>=self.F.shape[0]-1) | (cols<0) | (cols>=self.F.shape[1]-1)
        else:
            raise Exception,"bad interpolation type %s"%interpolation

        if rows.ndim > 0:
            rows[bad] = 0
            cols[bad] = 0
        elif bad:
            rows = cols = 0

        if interpolation == 'nearest':
            result = self.F[rows,cols]
        else:
            result =   self.F[rows,cols]    *(1.0-row_alpha)*(1.0-col_alpha) \
                     + self.F[rows+1,cols]  *row_alpha      *(1.0-col_alpha) \
                     + self.F[rows,cols+1]  *(1.0-row_alpha)*col_alpha \
                     + self.F[rows+1,cols+1]*row_alpha      *col_alpha

        # It may have been an int field, and now we need to go to float and set some nans:
        if result.dtype in (int,int8,int16,int32,int64):
            print "Converting from %s to float"%result.dtype
            result = result.astype(float64)
            result[ result==self.int_nan ] = nan
        if result.ndim>0:
            result[bad] = nan
        elif bad:
            result = nan

        # let linear interpolation fall back to nearest at the borders:
        if interpolation=='linear' and fallback and any(bad):
            result[bad] = self.interpolate(X[bad],interpolation='nearest',fallback=False)
            
        return result

    def value(self,X):
        return self.interpolate(X)

    def value_on_edge(self,e,samples=None):
        """ Return the value averaged along an edge - the generic implementation
        just takes 5 samples evenly spaced along the line, using value()
        """
        if samples is None:
            res = min(self.dx,self.dy)
            l = norm(e[1]-e[0])
            samples = int(ceil(l/res))

        return Field.value_on_edge(self,e,samples=samples)

    def upsample(self,factor=2):
        x = linspace(self.extents[0],self.extents[1],1+factor*(self.F.shape[1]-1))
        y = linspace(self.extents[2],self.extents[3],1+factor*(self.F.shape[0]-1))
        
        new_F = zeros( (len(y),len(x)) , float64 )

        for row in range(len(y)):
            for col in range(len(x)):
                new_F[row,col] = 0.25 * (self.F[row/2,col/2] +
                                         self.F[(row+1)/2,col/2] +
                                         self.F[row/2,(col+1)/2] +
                                         self.F[(row+1)/2,(col+1)/2])
        
        return SimpleGrid(self.extents,new_F)
    def downsample(self,factor):
        factor = int(factor)

        # use a really naive downsampling for now:
        new_F = array(self.F[::factor,::factor])

        x,y = self.xy()

        new_x = x[::factor]
        new_y = y[::factor]

        new_extents = [x[0],x[-1],y[0],y[-1]]

        return SimpleGrid(new_extents,new_F)

    ## Methods to fill in missing data
    def fill_by_griddata(self):
        """ Basically griddata - limits the input points to the borders 
        of areas missing data.
        Fills in everything within the convex hull of the valid input pixels.
        """
        
        # Find pixels missing one or more neighbors:
        valid = isfinite(self.F)
        all_valid_nbrs = ones(valid.shape,'bool')
        all_valid_nbrs[:-1,:] &= valid[1:,:] # to the west
        all_valid_nbrs[1:,:] &=  valid[:-1,:] # to east
        all_valid_nbrs[:,:-1] &= valid[:,1:] # to north
        all_valid_nbrs[:,1:] &= valid[:,:-1] # to south

        missing_a_nbr = valid & (~ all_valid_nbrs )

        i,j = nonzero(missing_a_nbr)

        x = arange(self.F.shape[0])
        y = arange(self.F.shape[1])

        values = self.F[i,j]

        # Try interpolating the whole field - works, but slow...
        fill_data = griddata(i,j,values,x,y)

        self.F[~valid] = fill_data[~valid]


    # Is there a clever way to use convolution here -
    def fill_by_convolution(self,iterations=7,smoothing=0,kernel_size=3):
        """  Better for filling in small seams - repeatedly
        applies a 3x3 average filter.  On each iteration it can grow
        the existing data out by 2 pixels.
        Note that by default there is not 
        a separate smoothing process - each iteration will smooth
        the pixels from previous iterations, but a pixel that is set
        on the last iteration won't get any smoothing.

        Set smoothing >0 to have extra iterations where the regions are not
        grown, but the averaging process is reapplied.

        If iterations is 'adaptive', then iterate until there are no nans.
        """
        kern = ones( (kernel_size,kernel_size) )

        valid = isfinite(self.F) 

        bin_valid = valid.copy()
        # newF = self.F.copy()
        newF = self.F # just do it in place
        newF[~valid] = 0.0

        if iterations=='adaptive':
            iterations=1
            adaptive=True
        else:
            adaptive=False

        i = 0
        while i < iterations+smoothing:
            #for i in range(iterations + smoothing):

            weights = signal.convolve2d(bin_valid,kern,mode='same',boundary='symm')
            values  = signal.convolve2d(newF,kern,mode='same',boundary='symm')

            # update data_or_zero and bin_valid
            # so anywhere that we now have a nonzero weight, we should get a usable value.

            # for smoothing-only iterations, the valid mask isn't expanded
            if i < iterations:
                bin_valid |= (weights>0)

            to_update = (bin_valid & (~valid)).astype(bool)
            newF[to_update] = values[to_update] / weights[to_update]

            i+=1
            if adaptive and (sum(~bin_valid)>0):
                iterations += 1 # keep trying
            else:
                adaptive = False # we're done 

        # and turn the missing values back to nan's
        newF[~bin_valid] = nan
    

    def mask_outside(self,poly,value=nan,invert=False,straddle=None):
        """ Set the values that fall outside the given polygon to the
        given value.  Existing nan values are untouched.

        straddle: if None, then only test against the center point
          if True: a pixel intersecting the poly, even if the center is not
          inside, is accepted.
          [future: False: reject straddlers]
        """
        if prep:
            poly = prep(poly)
            
        X,Y = self.xy()
        rect=array([[-self.dx/2.0,-self.dy/2.0],
                    [self.dx/2.0,-self.dy/2.0],
                    [self.dx/2.0,self.dy/2.0],
                    [-self.dx/2.0,self.dy/2.0]])
        for col in range(len(X)):
            print "%d/%d"%(col,len(X))
            for row in range(len(Y)):
                if isfinite(self.F[row,col]):
                    if straddle is None:
                        p = geometry.Point(X[col],Y[row])
                        if (not poly.contains(p)) ^ invert:# i hope that's the right logic
                            self.F[row,col] = value
                    elif straddle:
                        p = geometry.Polygon( array([X[col],Y[row]])[None,:] + rect )
                        if (not poly.intersects(p)) ^ invert:
                            self.F[row,col] = value

    def write(self,fname):
        fp = file(fname,'wb')

        cPickle.dump( (self.extents,self.F), fp, -1)
        fp.close()

    def write_gdal_rgb(self,output_file,vmin=None,vmax=None):
        if cm is None:
            raise Exception,"No matplotlib - can't map to RGB"
        
        if vmin is None:
            vmin = self.F.min()
        if vmax is None:
            vmax = self.F.max()
            
        fscaled = (self.F-vmin)/(vmax-vmin)
        frgba = (cm.jet(fscaled)*255).astype(uint8)

        # Create gtif
        driver = gdal.GetDriverByName("GTiff")
        dst_ds = driver.Create(output_file, self.F.shape[1], self.F.shape[0], 4, gdal.GDT_Byte,
                               ["COMPRESS=LZW"])

        # make nodata areas transparent:
        frgba[:,:,3] = 255*isfinite(self.F)

        # top left x, w-e pixel resolution, rotation, top left y, rotation, n-s pixel resolution
        # Gdal wants pixel-edge extents, but what we store is pixel center extents...
        dx,dy = self.delta()

        # Some GDAL utilities function better if the output is in image coordinates, so flip back
        # if needed
        if dy > 0:
            print "Flipping to be in image coordinates"
            dy = -dy
            frgba = frgba[::-1,:,:]

        dst_ds.SetGeoTransform( [ self.extents[0]-0.5*dx, dx,
                                  0, self.extents[3]-0.5*dy, 0, dy ] )

        # set the reference info
        if self.projection() is not None:
            srs = osr.SpatialReference()
            srs.SetWellKnownGeogCS(self.projection())
            dst_ds.SetProjection( srs.ExportToWkt() )

        # write the band
        for band in range(4):
            b1 = dst_ds.GetRasterBand(band+1)
            b1.WriteArray(frgba[:,:,band])
        dst_ds.FlushCache()

    gdalwarp = "gdalwarp" # path to command
    def warp_to_match(self,target):
        """
        Given a separte field trg, warp this one to match pixel for pixel.

        self and target should have meaningful projection().
        """
        # adjust for GDAL wanting to pixel edges, not
        # pixel centers
        halfdx = 0.5*target.dx
        halfdy = 0.5*target.dy
        te = "-te %f %f %f %f "%(target.extents[0]-halfdx,target.extents[2]-halfdy,
                                 target.extents[1]+halfdx,target.extents[3]+halfdy)
        ts = "-ts %d %d"%(target.F.T.shape) 
        
        return self.warp(target.projection(),
                         extra=te + ts)
        
    def warp(self,t_srs,s_srs=None,fn=None,extra=""):
        """ interface to gdalwarp
        t_srs: string giving the target projection
        s_srs: override current projection of the dataset, defaults to self._projection
        fn: if set, the result will retained, written to the given file.  Otherwise
          the transformation will use temporary files.        opts: other
        extra: other options to pass to gdalwarp
        """
        tmp_src = tempfile.NamedTemporaryFile(suffix='.tif',delete=False)
        tmp_src_fn = tmp_src.name ; tmp_src.close()
        
        if fn is not None:
            tmp_dest_fn = fn
        else:
            tmp_dest  = tempfile.NamedTemporaryFile(suffix='.tif',delete=False)
            tmp_dest_fn = tmp_dest.name
            tmp_dest.close()

        s_srs = s_srs or self.projection()
        self.write_gdal(tmp_src_fn)
        subprocess.call("%s -s_srs %s -t_srs %s -dstnodata 'nan' %s %s %s"%(self.gdalwarp,s_srs,t_srs,
                                                                            extra,
                                                                            tmp_src_fn,tmp_dest_fn),
                        shell=True)

        result = GdalGrid(tmp_dest_fn)
        os.unlink(tmp_src_fn)
        if fn is None:
            os.unlink(tmp_dest_fn)
        return result
        
    def write_gdal(self,output_file,nodata=None):
        """ Write a Geotiff of the field.
        Currently setting the projection doesn't appear to work...

        if nodata is specified, nan's are replaced by this value, and try to tell
        gdal about it.
        """
        # Create gtif
        driver = gdal.GetDriverByName("GTiff")
        gtype = numpy_type_to_gdal[self.F.dtype.type]
        dst_ds = driver.Create(output_file, self.F.shape[1], self.F.shape[0], 1, gtype,
                               ["COMPRESS=LZW"])
        raster = self.F

        if nodata is not None:
            raster = raster.copy()
            raster[ isnan(raster) ] = nodata

        # top left x, w-e pixel resolution, rotation, top left y, rotation, n-s pixel resolution
        # Gdal wants pixel-edge extents, but what we store is pixel center extents...
        dx,dy = self.delta()

        # Some GDAL utilities function better if the output is in image coordinates, so flip back
        # if needed
        if dy > 0:
            print "Flipping to be in image coordinates"
            dy = -dy
            raster = raster[::-1,:]
        
        dst_ds.SetGeoTransform( [ self.extents[0]-0.5*dx, dx,
                                  0, self.extents[3]-0.5*dy, 0, dy ] )

        # set the reference info
        if self.projection() is not None:
            srs = osr.SpatialReference()
            srs.SetWellKnownGeogCS(self.projection())
            dst_ds.SetProjection( srs.ExportToWkt() )

        # write the band
        b1 = dst_ds.GetRasterBand(1)
        if nodata is not None:
            b1.SetNoDataValue(nodata)
        else:
            # does this work?
            b1.SetNoDataValue(nan)
        b1.WriteArray(raster)
        dst_ds.FlushCache()

    def point_to_index(self,X):
        X=np.asarray(X)
        x = (X[...,0]-self.extents[0])/self.dx
        y = (X[...,1]-self.extents[2])/self.dy
        return array([y,x]).T

    def extract_tile(self,xxyy=None,res=None,match=None,interpolation='linear',missing=np.nan):
        """ Create the requested tile
        xxyy: a 4-element sequence
        match: another field, assumed to be in the same projection, to match
          pixel for pixel.

        interpolation: 'linear','quadratic','cubic' will pass the corresponding order
           to RectBivariateSpline.
         'bilinear' will instead use simple bilinear interpolation, will has the
         added benefit of preserving nans.
        """
        if match is not None:
            xxyy = match.extents
            resx,resy = match.delta()
            x,y = match.xy()
        else:
            if res is None:
                resx = resy = self.dx
            else:
                resx = resy = res
            x = arange(xxyy[0],xxyy[1]+resx,resx)
            y = arange(xxyy[2],xxyy[3]+resy,resy)
            
        myx,myy = self.xy()

        if interpolation == 'bilinear':
            F=self.F
            def interper(y,x):
                # this is taken from a stack overflow answer
                #  "simple-efficient-bilinear-interpolation-of-images-in-numpy-and-python"
                # but altered so that x and y are 1-D arrays, and the result is a
                # 2-D array (x and y as in inputs to meshgrid)
                
                # scale those to float-valued indices into F
                x = (np.asarray(x)-self.extents[0])/self.dx
                y = (np.asarray(y)-self.extents[2])/self.dy

                x0 = np.floor(x).astype(int)
                x1 = x0 + 1
                y0 = np.floor(y).astype(int)
                y1 = y0 + 1

                x0 = np.clip(x0, 0, F.shape[1]-1)
                x1 = np.clip(x1, 0, F.shape[1]-1)
                y0 = np.clip(y0, 0, F.shape[0]-1)
                y1 = np.clip(y1, 0, F.shape[0]-1)

                Ia = F[ y0,:][:, x0 ]
                Ib = F[ y1,:][:, x0 ]
                Ic = F[ y0,:][:, x1 ]
                Id = F[ y1,:][:, x1 ]

                wa = (x1-x)[None,:] * (y1-y)[:,None]
                wb = (x1-x)[None,:] * (y-y0)[:,None]
                wc = (x-x0)[None,:] * (y1-y)[:,None]
                wd = (x-x0)[None,:] * (y-y0)[:,None]

                result = wa*Ia + wb*Ib + wc*Ic + wd*Id
                result[ y<0,: ] = missing
                result[ y>F.shape[0],: ] = missing
                result[ :, x<0 ] = missing
                result[ :, x>F.shape[1]] = missing
                return result
        else:
            k = ['constant','linear','quadratic','cubic'].index(interpolation)

            
            if any(isnan(self.F)):
                F = self.F.copy()
                F[ isnan(F) ] = 0.0
            else:
                F = self.F

            # Unfortunately this doesn't respect nan values in F
            interper = RectBivariateSpline(x=myy,y=myx,z=F,kx=k,ky=k)

        # limit to where we actually have data:
        # possible 0.5dx issues here
        xbeg,xend = searchsorted(x,self.extents[:2])
        ybeg,yend = searchsorted(y,self.extents[2:])
        Ftmp = ones( (len(y),len(x)),dtype=self.F.dtype)
        Ftmp[...] = missing
        # This might have some one-off issues
        Ftmp[ybeg:yend,xbeg:xend] = interper(y[ybeg:yend],x[xbeg:xend])
        return SimpleGrid(extents=xxyy,
                          F=Ftmp)

    def gradient(self):
        """ compute 2-D gradient of the field, returning a pair of fields of the
        same size (one-sided differences are used at the boundaries, central elsewhere).
        returns fields: dFdx,dFdy
        """
        # make it the same size, but use one-sided stencils at the boundaries
        dFdx = zeros(self.F.shape,float64)
        dFdy = zeros(self.F.shape,float64)

        # central difference in interior:
        dFdx[:,1:-1] = (self.F[:,2:] - self.F[:,:-2]) /(2*self.dx)
        dFdy[1:-1,:] = (self.F[2:,:] - self.F[:-2,:]) /(2*self.dy)

        # one-sided at boundaries:
        dFdx[:,0] = (self.F[:,1] - self.F[:,0])/self.dx
        dFdx[:,-1] = (self.F[:,-1] - self.F[:,-2])/self.dx
        dFdy[0,:] = (self.F[1,:] - self.F[0,:])/self.dy
        dFdy[-1,:] = (self.F[-1,:] - self.F[-2,:])/self.dy

        dx_field = SimpleGrid(extents = self.extents,F = dFdx)
        dy_field = SimpleGrid(extents = self.extents,F = dFdy)
        return dx_field,dy_field

        

    @staticmethod
    def read(fname):
        fp = file(fname,'rb')

        extents, F = cPickle.load( fp )

        fp.close()
        
        return SimpleGrid(extents=extents,F=F)


class GtxGrid(SimpleGrid):
    def __init__(self,filename,is_vertcon=False,missing=9999,projection='WGS84'):
        """ loads vdatum style binary gtx grids
        is_vertcon: when true, adjusts values from mm to m
        """
        self.filename = filename
        fp=open(self.filename,'rb')

        ll_lat,ll_lon,delta_lat,delta_lon = fromstring(fp.read(4*8),'>f8')
        ll_lon = (ll_lon + 180)%360. - 180

        nrows,ncols = fromstring(fp.read(2*4),'>i4')

        heights = fromstring(fp.read(nrows*ncols*8),'>f4').reshape( (nrows,ncols) )
        heights = heights.byteswap().newbyteorder().astype(float64).copy() # does this fix byte order?
        
        heights[ heights == missing ] = nan
        if is_vertcon:
            heights *= 0.001 # vertcon heights in mm

        # pretty sure that the corner values from the GTX file are
        # node-centered, so no need here to pass half-pixels around.
        SimpleGrid.__init__(self,
                            extents = [ll_lon,ll_lon+(ncols-1)*delta_lon,ll_lat,ll_lat+(nrows-1)*delta_lat],
                            F = heights,
                            projection=projection) 

class GdalGrid(SimpleGrid):
    @staticmethod
    def metadata(filename):
        """ Return the extents and resolution without loading the whole file
        """
        gds = gdal.Open(filename)
        (x0, dx, r1, y0, r2, dy ) = gds.GetGeoTransform()
        nx = gds.RasterXSize
        ny = gds.RasterYSize

        # As usual, this may be off by a half pixel...
        x1 = x0 + nx*dx
        y1 = y0 + ny*dy

        xmin = min(x0,x1)
        xmax = max(x0,x1)
        ymin = min(y0,y1)
        ymax = max(y0,y1)
        
        return [xmin,xmax,ymin,ymax],[dx,dy]

    def __init__(self,filename,bounds=None,geo_bounds=None):
        """ Load a raster dataset into memory.
        bounds: [x-index start, x-index end, y-index start, y-index end]
         will load a subset of the raster.

        geo_bounds: xxyy bounds in geographic coordinates 
        """
        self.gds = gdal.Open(filename)
        (x0, dx, r1, y0, r2, dy ) = self.gds.GetGeoTransform()

        if geo_bounds is not None:
            # convert that the index bounds:
            ix_start = int( float(geo_bounds[0]-x0)/dx )
            ix_end = int( float(geo_bounds[1]-x0)/dx)+ 1
            # careful about sign of dy
            if dy>0:
                iy_start = int( float(geo_bounds[2]-y0)/dy )
                iy_end   = int( float(geo_bounds[3]-y0)/dy ) + 1
            else:
                iy_start = int( float(geo_bounds[3]-y0)/dy )
                iy_end   = int( float(geo_bounds[2]-y0)/dy ) + 1

            bounds = [ix_start,ix_end,
                      iy_start,iy_end]
            print "geo bounds gave bounds",bounds
            self.geo_bounds = geo_bounds
            
        self.subset_bounds = bounds
        
        if bounds:
            A = self.gds.ReadAsArray(xoff = bounds[0],yoff=bounds[2],
                                     xsize = bounds[1] - bounds[0],
                                     ysize = bounds[3] - bounds[2])
            # and doctor up the metadata to reflect this:
            x0 += bounds[0]*dx
            y0 += bounds[2]*dy
        else:
            A = self.gds.ReadAsArray()

        # A is rows/cols !
        # And starts off with multiple channels, if they exist, as the
        # first index.
        if A.ndim == 3:
            print "Putting multiple channels as last index"
            A = A.transpose(1,2,0)

        # often gdal data is in image coordinates, which is just annoying.
        # Funny indexing because sometimes there are multiple channels, and those
        # appear as the first index:
        Nrows = A.shape[0]
        Ncols = A.shape[1]
        
        if dy < 0:
            # make y0 refer to the bottom left corner
            # and dy refer to positive northing
            y0 = y0 + Nrows*dy
            dy = -dy
            A = A[...,::-1,:]

        # and there might be a nodata value, which we want to map to NaN
        b = self.gds.GetRasterBand(1)
        nodata = b.GetNoDataValue()

        if A.dtype in (int16,int32):
            A[ A==nodata ] = self.int_nan
        else:
            A[ A==nodata ] = nan

        SimpleGrid.__init__(self,
                            extents = [x0+0.5*dx,
                                       x0+0.5*dx + dx*(Ncols-1),
                                       y0+0.5*dy,
                                       y0+0.5*dy + dy*(Nrows-1)],
                            F=A,
                            projection=self.gds.GetProjection() )


import interp_coverage
class BlenderField(Field):
    """ Delegate to sub-fields, based on polygons in a shapefile, and blending
    where polygons overlap.

    If delegates is specified:
      The shapefile is expected to have a field 'name', which is then used to
      index the dict to get the corresponding field.

    Alternatively, if a factory is given, it should be callable and will take a single argument -
    a dict with the attributse for each source.  The factory should then return the corresponding
    Field.
    """
    def __init__(self,shp_fn,delegates=None,factory=None):
        self.shp_fn = shp_fn
        
        self.ic = interp_coverage.InterpCoverage(shp_fn)
        Field.__init__(self)

        self.delegates = delegates
        self.factory = factory
        
        self.delegate_list = [None]*len(self.ic.regions)
        
        # get the delegates into the same order as expected by InterpCoverage
        # DELAY this until it's needed
        # for r in self.ic.regions:
        #     if delegates is not None:
        #         delegate = delegates[r.items['name']] 
        #     else:
        #         delegate = factory( r.items )
        #     self.delegates.append(delegate)

    def bounds(self):
        raise Exception,"For now, you have to specify the bounds when gridding a BlenderField"

    def load_region(self,i):
        # for r in self.ic.regions:
        r = self.ic.regions[i]
        
        if self.delegates is not None:
            d = self.delegates[r.items['name']] 
        else:
            d = self.factory( r.items )
            
        self.delegate_list[i] = d
        
    def value(self,X):
        print "Calculating weights"
        weights = self.ic.calc_weights(X)
        total_weights = weights.sum(axis=-1)

        vals = zeros(X.shape[:-1],float64)
        vals[total_weights==0.0] = nan

        # iterate over sources:
        for src_i in range(len(self.delegate_list)):
            print "Processing layer ",self.ic.regions[src_i].identifier()
            
            src_i_weights = weights[...,src_i]
            
            needed = (src_i_weights != 0.0)
            if needed.sum() > 0:
                if self.delegate_list[src_i] is None:
                    self.load_region(src_i) # lazy loading
                src_vals = self.delegate_list[src_i].value( X[needed] )
                vals[needed] += src_i_weights[needed] * src_vals
        return vals

    def value_on_edge(self,e):
        """ Return the interpolated value for a given line segment"""
        ### UNTESTED
        c = e.mean(axis=0) # Center of edge
        
        weights = self.ic.calc_weights(c)
        
        val = 0.0 # zeros(X.shape[:-1],float64)

        # iterate over sources:
        for src_i in range(len(self.delegate_list)):
            # print "Processing layer ",self.ic.regions[src_i].identifier()
            
            src_i_weight = weights[src_i]
            
            if src_i_weight != 0.0:
                if self.delegate_list[src_i] is None:
                    self.load_region(src_i)
                src_val = self.delegate_list[src_i].value_on_edge( e )
                val += src_i_weight * src_val
        return val

    def diff(self,X):
        """ Calculate differences between datasets where they overlap:
        When a point has two datasets, the first is subtracted from the second.
        When there are more, they alternate - so with three, you get A-B+C
        Not very useful, but fast...
        """
        weights = self.ic.calc_weights(X)

        vals = zeros(X.shape[:-1],float64)

        used = (weights!=0.0)
        n_sources = used.sum(axis=-1)

        # We just care about how the sources differ - if there is only
        # one source then don't even bother calculating it - set all weights
        # to zero.
        weights[(n_sources==1),:] = 0.0 #

        # iterate over sources:
        for src_i in range(len(self.delegates)):
            src_i_weights = weights[...,src_i]
            
            needed = (src_i_weights != 0.0)
            src_vals = self.delegates[src_i].value( X[needed] )
            vals[needed] = src_vals - vals[needed] 
        return vals
        


class MultiRasterField(Field):
    """ Given a collection of raster files at various resolutions and with possibly overlapping
    extents, manage a field which picks from the highest resolution raster for any given point.

    Assumes that any point of interest is covered by at least one field (though there may be slower support
    coming for some sort of nearest valid usage).

    There is no blending for point queries!  If two fields cover the same spot, the value taken from the
    higher resolution field will be returned.

    Basic bilinear interpolation will be utilized for point queries.

    Edge queries will resample the edge at the resolution of the highest datasource, and then proceed with
    those point queries

    Cell/region queries will have to wait for another day

    Some effort is made to keep only the most-recently used rasters in memory, since it is not feasible
    to load all rasters at one time. to this end, it is most efficient for successive queries to have some
    spatial locality.
    """

    # If finite, any point sample greater than this value will be clamped to this value
    clip_max = inf

    order = 1 # interpolation order
    
    # After clipping, this value will be added to the result.
    # probably shouldn't use this - domain.py takes care of adding in the bathymetry offset
    # and reversing the sign (so everything becomes positive)
    offset = 0.0 # BEWARE!!! read the comment.
    
    def __init__(self,raster_file_patterns,**kwargs):
        self.__dict__.update(kwargs)
        Field.__init__(self)
        raster_files = []
        for patt in raster_file_patterns:
            raster_files += glob.glob(patt)
        
        self.raster_files = raster_files

        self.prepare()

    def bounds(self):
        """ Aggregate bounds """
        all_extents = array(self.extents)

        return [ all_extents[:,0].min(),
                 all_extents[:,1].max(),
                 all_extents[:,2].min(),
                 all_extents[:,3].max() ]

    def prepare(self):
        # find extents and resolution of each dataset:
        extents = []
        resolutions = []

        for f in self.raster_files:
            extent,resolution = GdalGrid.metadata(f)
            extents.append(extent)
            resolutions.append( max(resolution[0],resolution[1]) )

        self.extents = extents
        self.resolutions = array(resolutions)
        
        self.sources = [None]*len(self.raster_files)
        # -1 means the source isn't loaded.  non-negative means it was last used when serial
        # was that value.  overflow danger...
        self.last_used = -1 * ones( len(self.raster_files), int32)

        self.build_index()

    def build_index(self):
        # Build a basic index that will return the overlapping dataset for a given point
        ext = self.extents # those are x,x,y,y
        tuples = [(i,ext[i],None) for i in range(len(ext))]
        
        self.index = Rtree(tuples,interleaved=False)

    max_count = 20 
    open_count = 0
    serial = 0
    def source(self,i):
        """ LRU based cache of the datasets
        """
        if self.sources[i] is None:
            if self.open_count >= self.max_count:
                # Have to choose someone to close.
                current = nonzero(self.last_used>=0)[0]
                
                victim = current[ argmin( self.last_used[current] ) ]
                # print "Will evict source %d"%victim
                self.last_used[victim] = -1
                self.sources[victim] = None
                self.open_count -= 1
            # open the new guy:
            self.sources[i] = GdalGrid(self.raster_files[i])
            self.open_count += 1
            
        self.serial += 1
        self.last_used[i] = self.serial
        return self.sources[i]
        
    def value_on_point(self,xy):
        hits = self.index.intersection( xy[xxyy] )
        
        if isinstance(hits, types.GeneratorType):
            # so translate that into a list like we used to get.
            hits = list(hits)
        hits = array(hits)
        if len(hits) == 0:
            return nan
        # print "Hits: ",hits
        
        hits = hits[ argsort( self.resolutions[hits] ) ]
            
        v = nan
        for hit in hits:
            src = self.source(hit)
            
            # Here we should be asking for some kind of basic interpolation
            v = src.interpolate( array([xy]), interpolation='linear' )[0]

            if isnan(v):
                continue
            if v > self.clip_max:
                v = self.clip_max
            return v

        print "Bad sample at point ",xy
        return v
            
    def value(self,X):
        """ X must be shaped (...,2)
        """
        X = array(X)
        orig_shape = X.shape

        X = reshape(X,(-1,2))

        newF = zeros( X.shape[0],float64 )
        
        for i in range(X.shape[0]):
            if i > 0 and i % 2000 == 0:
                print "%d/%d"%(i,X.shape[0])
            newF[i] = self.value_on_point( X[i] )
        
        newF = reshape( newF, orig_shape[:-1])
        
        if newF.ndim == 0:
            return float(newF)
        else:
            return newF

    def value_on_edge(self,e,samples=None):
        """
        Subsample the edge, using an interval based on the highest resolution overlapping
        dataset.  Average and return...
        """

        pmin = e.min(axis=0)
        pmax = e.max(axis=0)
        
        hits = self.index.intersection( [pmin[0],pmax[0],pmin[1],pmax[1]] )
        if isinstance(hits, types.GeneratorType):
            hits = list(hits)
        hits = array(hits)

        if len(hits) == 0:
            return nan

        # Order them based on resolution:
        hits = hits[ argsort( self.resolutions[hits] ) ]
        res = self.resolutions[hits[0]]

        samples = int( ceil( norm(e[0] - e[1])/res) )
        
        x=linspace(e[0,0],e[1,0],samples)
        y=linspace(e[0,1],e[1,1],samples)

        X = array([x,y]).transpose()

        # old way - about 1.3ms per edge over 100 edges
        # return nanmean(self.value(X))

        # inlining -
        # in order of resolution, query all the points at once from each field.
        edgeF = nan*ones( X.shape[0],float64 )

        for hit in hits:
            missing = isnan(edgeF)
            src = self.source(hit)

            # for the moment, keep the nearest interpolation
            edgeF[missing] = src.interpolate( X[missing],interpolation='linear' )
            if all(isfinite(edgeF)):
                break

        edgeF = clip(edgeF,-inf,self.clip_max) # ??
        return nanmean(edgeF)
    
    def extract_tile(self,xxyy,res=None):
        """ Create the requested tile from merging the sources.  Resolution defaults to
        resolution of the highest resolution source that falls inside the requested region
        """
        hits = self.index.intersection( xxyy )
        if isinstance(hits, types.GeneratorType):
            # so translate that into a list like we used to get.
            hits = list(hits)
        hits = array(hits)

        if len(hits) == 0:
            return None
        
        hits = hits[ argsort( self.resolutions[hits] ) ]
        
        if res is None:
            res = self.resolutions[hits[0]]

        # half-pixel alignment-
        # field.SimpleGrid expects extents which go to centers of pixels.
        # x and y are inclusive of the end pixels (so for exactly abutting rects, there will be 1 pixel
        # of overlap)
        x=arange( xxyy[0],xxyy[1]+res,res)
        y=arange( xxyy[2],xxyy[3]+res,res)
        targetF = nan*zeros( (len(y),len(x)), float64)
        pix_extents = [x[0],x[-1], y[0],y[-1] ]
        target = SimpleGrid(extents=pix_extents,F=targetF)

        # iterate over overlapping DEMs until we've filled in all the holes
        # might want some feature where coarse data are only input at their respective
        # cell centers, and then everything is blended,
        # or maybe that as dx increases, we allow a certain amount of blending first
        # the idea being that it there's a 5m hole in some lidar, it's better to blend the
        # lidar than to query a 100m dataset.


        # extend the extents to consider width of pixels (this at least makes the output
        # register with the inputs)

        # fig,(ax1,ax2) = subplots(2,1,sharex=1,sharey=1)

        for hit in hits:
            src = self.source(hit)
            # src.plot(ax=ax1,vmin=0,vmax=5,interpolation='nearest')

            src_x,src_y = src.xy()
            src_dx,src_dy = src.delta()

            # maps cols in the destination to cols in this source
            # map_coordinates wants decimal array indices
            # x has the utm easting for each column to extract
            # x-src_x:   easting relative to start of src tile
            # 
            dec_x = (x-src_x[0]) / src_dx
            dec_y = (y-src_y[0]) / src_dy

            if self.order==0:
                dec_x = floor( (dec_x+0.5) )
                dec_y = floor( (dec_y+0.5) )

            # what range of the target array falls within this tile
            col_range = nonzero( (dec_x>=0) & (dec_x <= len(src_x)-1))[0]
            if len(col_range):
                col_range = col_range[ [0,-1]]
            else:
                continue
            row_range = nonzero( (dec_y>=0) & (dec_y <= len(src_y)-1))[0]
            if len(row_range):
                row_range=row_range[ [0,-1]]
            else:
                continue
            
            col_slice = slice(col_range[0],col_range[1]+1)
            row_slice = slice(row_range[0],row_range[1]+1)
            dec_x = dec_x[ col_slice ]
            dec_y = dec_y[ row_slice ]

            C,R = meshgrid( dec_x,dec_y )

            newF = map_coordinates(src.F, [R,C],order=self.order)

            # only update missing values
            missing = isnan(target.F[ row_slice,col_slice ])
            target.F[ row_slice,col_slice ][missing] = newF[missing]
        return target
        
    
class FunctionField(Field):
    """ wraps an arbitrary function
    function must take one argument, X, which has
    shape [...,2]
    """
    def __init__(self,func):
        self.func = func
    def value(self,X):
        return self.func(X)

if __name__ == '__main__':
    topobathy = "/home/rusty/classes/research/spatialdata/us/ca/suntans/bathymetry/ca-topobathy/85957956/85957956/hdr.adf"
    corrected_fn = "/home/rusty/classes/research/spatialdata/us/ca/suntans/bathymetry/usgs/southbay-corrected.xyz"

    corrected = XYZText(corrected_fn,projection="EPSG:26910")
    corrected2 = corrected.rectify()

    tile = GdalGrid(topobathy)

    zoom_ll = corrected.bounds_in_cs(tile.projection())

    tile_cropped = tile.crop(zoom_ll)

    tile_utm = tile_cropped.reproject(to_projection=corrected.projection())



    # arithmetic interface may change...
    # diff = tile_utm - corrected2
    corr_on_tile = tile_utm.regrid(corrected2)

    corr_cv = CurvilinearGrid(tile_utm.X,corr_on_tile,projection=tile_utm.projection())


    subplot(211)
    corrected.plot(vmin=-10,vmax=2)

    subplot(212)
    tile_utm.plot(vmin=-10,vmax=2)

