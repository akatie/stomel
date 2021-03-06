# import field
from numpy import *
from numpy.linalg import norm


def as_density(d):
    #if not isinstance(density,field.Field):
    #    density = field.ConstantField(density)

    if type(d) in [float,int]:
        orig_density = d
        d = lambda X: orig_density * ones(X.shape[:-1])
    return d
    

def upsample_linearring(points,density,closed_ring=1,return_sources=False):
    new_segments = []

    sources = []

    density = as_density(density)
        
    for i in range(len(points)):
        A = points[i]
        
        if i+1 == len(points) and not closed_ring:
            new_segments.append( [A] )
            sources.append( [i] )
            break
            
        B = points[(i+1)%len(points)]

        l = norm(B-A)
        # print "Edge length is ",l

        scale = density( 0.5*(A+B) )

        # print "Scale is ",scale
        
        npoints = max(1,round( l/scale ))
        
        # print "N points ",npoints

        alphas = arange(npoints) / float(npoints)

        new_segment = (1.0-alphas[:,newaxis])*A + alphas[:,newaxis]*B
        new_segments.append(new_segment)
        sources.append(i + alphas)

    new_points = concatenate( new_segments )

    if return_sources:
        sources = concatenate(sources)
        # print "upsample: %d points, %d alpha values"%( len(new_points), len(sources))
        return new_points,sources
    else:
        return new_points

        
def downsample_linearring(points,density,factor=None,closed_ring=1):
    """ Makes sure that points aren't *too* close together
    Allow them to be 0.3*density apart, but any edges shorter than that will
    lose one of their endpoints.
    """
    if factor is not None:
        density = density * factor # should give us a BinOpField
    density = as_density(density)

    valid = ones( len(points), 'bool8')

    # go with a slower but safer loop here -
    last_valid=0
    for i in range(1,len(points)):
        scale = density( 0.5*(points[last_valid] + points[i]) )
        
        if norm( points[last_valid]-points[i] ) < scale:
            if i==len(points)-1:
                # special case to avoid moving the last vertex
                valid[last_valid] = False
                last_valid = i
            else:
                valid[i] = False
        else:
            last_valid = i

    return points[valid]

    
def resample_linearring(points,density,closed_ring=1,return_sources=False):
    """  similar to upsample, but does not try to include
    the original points, and can handle a density that changes
    even within one segment of the input
    """
    density = as_density(density)
    
    if closed_ring:
        points = concatenate( (points, [points[0]]) )
    
    # distance_left[i] is the distance from points[i] to the end of
    # the line, along the input path.
    lengths = sqrt( ((points[1:] - points[:-1])**2).sum(axis=1) )
    distance_left = cumsum( lengths[::-1] )[::-1]

    
    new_points = []
    new_points.append( points[0] )

    # x=sources[i] means that the ith point is between points[floor(x)]
    # and points[floor(x)+1], with the fractional step between them
    #  given by x%1.0
    sources = [0.0]

    # indexes the ending point of the segment we're currently sampling
    # the starting point is just new_points[-1]
    i=1

    # print "points.shape ",points.shape
    while 1:
        # print "Top of loop, i=",i
        
        last_point = new_points[-1]
        last_source = sources[-1]

        if i < len(distance_left):
            total_distance_left = norm(points[i] - last_point) + distance_left[i]
        else:
            total_distance_left = norm(points[i] - last_point)

            
        scale = density( last_point )
        npoints_at_scale = round( total_distance_left/scale )

        if npoints_at_scale <= 1:
            break
        
        this_step_length = total_distance_left / npoints_at_scale
        # print "scale = %g   this_step_length = %g "%(scale,this_step_length)

        # at this point this_step_length refers to how far we must go
        # from new_points[i], along the boundary.

        while norm( points[i] - last_point ) < this_step_length:
            # print "i=",i
            this_step_length -= norm( points[i] - last_point )
            last_point = points[i]
            last_source = float(i)
            i += 1


        seg_length = norm(points[i] - points[i-1])
        # along this whole segment, we might be starting in the middle
        # from a last_point that was on this same segment, in which
        # case add our alpha to the last alpha
        # print "[%d,%d] length=%g   step_length=%g "%(i-1,i,seg_length,this_step_length)

        alpha = this_step_length / seg_length
        # print "My alpha", alpha
        last_alpha = (last_source  - floor(last_source))
        # print "Alpha from last step:",last_alpha
        alpha = alpha + last_alpha

        new_points.append( (1-alpha)*points[i-1] + alpha * points[i] )
        
        frac = norm(new_points[-1] - points[i-1])/ norm(points[i] - points[i-1])

        # print "frac=%g   alpha = %g"%(frac,alpha)
        
        sources.append( (i-1) + frac )
        
            
    new_points = array( new_points )

    if return_sources:
        sources = array( sources )
        return new_points,sources
    else:
        return new_points
