import subprocess
import argparse
import string
import os
import pprint

try:
   from pyne import material
   from pyne.material import Material, MaterialLibrary
except:
    raise ImportError("The PyNE dependencies could not be imported.")    

try:
    from pyne import dagmc
except:
    raise ImportError("The dagmc dependency could not be imported.")    

from pyne import data
from pyne import nucname
import numpy as np
import hzetrn as one_d_tool
import tag_utils

class DagmcError(Exception):
    pass

def load_ray_start(filename):
    ray_start = []
    if not filename:
        ray_start = [0.0, 0.0, 0.0]
    else:
        with open(filename) as f:
	    for line in f:
	        ray_start = map(float, line.split())
    pprint.pprint(ray_start)
    return ray_start

"""
Load or create tuples representing dirs
In file with 10,000 tuples, here are the ranges
('min_theta', 0.00117047, 'max_theta', 3.136877355, 'min_phi', -3.141219364, 'max_phi', 3.14087921)
"""
def load_ray_tuples(filename):

    ray_tuples = []

    # ToDo: randomly generate dirs
    if not filename:
       ray_tuples = [(1.0, 0.0, 0.0),
                     (0.0, 1.0, 0.0),
	   	     (0.0, 0.0, 1.0)]
    else:
       with open(filename) as f:
           for line in f:
	       nums = map(float, line.split())
	       z = np.cos(nums[0])
	       x = np.sin(nums[0])*np.cos(nums[1])
	       y = np.sin(nums[0])*np.sin(nums[1])
	       ray_tuples.append((x, y, z))
       print(filename, 'ray_tuples  x             y                z')
       pprint.pprint(ray_tuples)
    return ray_tuples

"""
Load a subset of tuples representing directions
This loads directions within a tolerance of the z-plane
"""
def subset_ray_tuples(filename):
    side = 10.0
    xlate = 20.0
    adj = xlate - side/2
    opp   = side/2
    limit = np.arctan(opp/adj)/2
    limitfac = 32.0

    ray_tuples = []

    print('The ray tuples file is', filename)
    with open(filename) as f:
        for line in f:
	    # Get the list of numbers from the line
            nums = map(float, line.split())
	    theta = nums[0]
	    phi   = nums[1]
	    # limit limit by another factor of 2 to get a smaller subset
	    if (np.pi/2 - limit/limitfac < theta) and (theta < np.pi/2 + limit/limitfac):
	        z = np.cos(nums[0])
	        x = np.sin(nums[0])*np.cos(nums[1])
	        y = np.sin(nums[0])*np.sin(nums[1])
	        ray_tuples.append((x, y, z))
        print ('num of rays within 1/', limitfac, 'of x-y plane', len(ray_tuples))
    return ray_tuples

"""
Produce a length-one tuple randomly oriented on a sphere
"""
def get_rand_dir():
    # [0.0,1.0)
    rnum = np.random.uniform()
    # map rnum onto [0.0,1.0)
    z = 2*rnum - 1
    theta = 2*np.pi*rnum
    norm_fac = np.sqrt(1 - z*z)
    y = norm_fac*np.sin(theta)
    x = norm_fac*np.cos(theta)
    return [x, y, z]


""" 
Find the vol-id of the geometry that contains the ref_point
"""
def find_ref_vol(ref_point):
    # code snippet from dagmc.pyx:find_graveyard_inner_box
    volumes = dagmc.get_volume_list() 
    ref_vol = 0 
    for v in volumes:
        if dagmc.point_in_volume(v, ref_point):
            ref_vol = v

    return ref_vol

""" 
xs_create_header creates a string conting the first few material-
independent lines of the input file for the cross-section call

returns string contining the lines
Note: this is not strictly a header, as what1 and what 2 have to match
the actual directories.
"""
def xs_create_header():
    what1 = 'common_static_data'
    what2 = 'cross_sections_out'
    comment1 = '# Name of folder where static data is stored'
    comment2 = '# Name of folder to put data in (folder must already exist)'
    # 84 dashes
    divider  = '{:-<84}'.format('-')
    # right-align, pad with spaces
    lines  = '{:<36}'.format(what1) + comment1 + '\n'
    lines += '{:<36}'.format(what2) + comment2 + '\n'
    lines += '\n'
    lines += divider + '\n'
    lines += '\n'
    return lines

""" xs_create_entries_from_lib
From the MaterialLibrary taken from the geometry file extract all the
information needed for the cross-section input file.
ToDo:  formatting of species line members
"""
def xs_create_entries_from_lib(mat_lib):
    # For each material create an entry and append to input_filename
    material_entries = []
    num_materials = len(mat_lib.keys())
    for key in mat_lib.iterkeys():
    # for key in mat_lib:
   	material_obj = mat_lib.get(key)
	# material_obj = mat_lib[key]
	print ('xs_create_entries_from_lib: collapsed material_obj:')
	# Cuckoo
	if 'Water' in key:
           h2o_atoms = {'H1': 2.0, 'O16': 1.0}
	   material_obj.from_atom_frac(h2o_atoms)
	# ToDo:  what materials do we not want to collapse?
	coll = material_obj.collapse_elements([])
	name1 = coll.metadata['name']
	# ToDo: Need a check here, this assumes a colon-delimited name.
	# Currently tallys are not seen here.  
	name = name1.split(':', 1)[1]
	density = coll.density
	num_species = len(coll.comp)
	material_entry = name + '\n' + str(density) + '\n' + str(num_species) + '\n'
	print coll
	for key in coll.comp:
	    compname = nucname.name(key)
	    str_comp_atomic_mass = str(data.atomic_mass(key))
	    str_comp_charge = str(nucname.znum(key))
	    str_comp_atoms_per_g = str(coll.comp[key]*data.N_A/data.atomic_mass(key))
	    print compname, str_comp_atomic_mass, str_comp_charge, str_comp_atoms_per_g
	    material_entry += str_comp_atomic_mass + '  ' + str_comp_charge + '  ' + str_comp_atoms_per_g + '\n'
        material_entries.append(material_entry)
    return material_entries

"""
Do all the work of finding the slab distances and material names
start_vol  The volume containing the ref_point
ref_point  the origin of the ray
dir 	   the direction of the ray
mat_for_vol a mapping of vol id to material object

returns    arrays  for length and material name as the ray
           travels from the start point to the graveyard

This depends on dagmc's ray_iterator, which calls ray_fire
"""
def slabs_for_dir(start_vol, ref_point, dir, mat_for_vol):
    is_graveyard = False
    foundMat = False
    surf = 0
    dist = 0
    huge = 1000000000

    slab_length = []
    slab_mat_name = []
    vols_traversed = []

    last_vol = start_vol
    for (vol, dist, surf, xyz) in dagmc.ray_iterator(start_vol, ref_point, dir, yield_xyz=True):
        if not is_graveyard and ((dist < huge) and (surf != 0)):
	    slab_length.append(dist)
	    if last_vol in mat_for_vol:
	        name1 = mat_for_vol[last_vol]
		name = name1.split(':', 1)[1]
            else:
	        name = 'implicit_complement'
	    if 'graveyard' in name:
	        is_graveyard = True
	    
	    slab_mat_name.append(name)
            vols_traversed.append(last_vol)
            last_vol = vol

    #########################################################################
    sdir = [format(dir[0],'.6f'),format(dir[1], '.6f'), format(dir[2], '.6f')]
    print ('for dir', sdir, 'vols traversed', vols_traversed)
    #########################################################################

    return slab_length, slab_mat_name 
      
"""
Argument parsing
returns : args: -f for the input geometry file, -r for the input ray tuple file
ref     : DAGMC/tools/parse_materials/dagmc_get_materials.py
"""
def parsing():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '-f', action='store', dest='uwuw_file', 
	help='The relative path to the .h5m file')

    parser.add_argument(
        '-r', action='store', dest='ray_dir_file', 
	help='The path to the file with ray direction tuples')

    parser.add_argument(
        '-p', action='store', dest='ray_start', 
	help='Cartesion coordinate of starting point of all rays')

    args = parser.parse_args()

    if not args.uwuw_file:
        raise Exception('h5m file path not specified. [-f] not set')
    if not args.ray_dir_file:
        args.ray_dir_file = False

    return args


def main():
    # Setup: parse the the command line parameters
    args = parsing()
    path = os.path.join(os.path.dirname('__file__'), args.uwuw_file)


    # Start the file with header lines which contain the names of 
    # some folders the cross_section processing will need
    # ToDo: These may be hard-coded to start with; 
    xs_header = xs_create_header()

    # Cross-Section: load the material library from the uwuw geometry file
    mat_lib = material.MaterialLibrary()
    mat_lib.from_hdf5(path)
    xs_material_entries = xs_create_entries_from_lib(mat_lib)

    # Prepare xs_input.dat
    xs_input_filename = 'xs_input.dat'
    f = open(xs_input_filename, 'w')
    f.write(xs_header)
    f.write("\n".join(xs_material_entries))
    f.close()

    # Using the input file just created, prepare the materials subdirectory
    # This method will make a subprocess call
    one_d_tool.cross_section_process(xs_input_filename)
    ##########################################################
    
    # Load the DAG object for this geometry
    rtn = dagmc.load(path)

    vol_mat_dict = tag_utils.get_mat_tag_values(path)
    print ('vol_mat_dict  ', vol_mat_dict)
    # get list of rays
    ray_tuples = load_ray_tuples(args.ray_dir_file) 
    # ray_tuples = subset_ray_tuples(args.ray_dir_file) 

    # The default starting point is 0,0,0
    ref_point = load_ray_start(args.ray_start)
    start_vol = find_ref_vol(ref_point)
    
    i=1
    for dir in ray_tuples:
	slab_length, slab_mat_name  = slabs_for_dir(start_vol, ref_point, dir, vol_mat_dict)
        #############################################

	transport_input = []
	num_mats = len(slab_mat_name)
	transport_input.append(str(num_mats))
	for n in range(num_mats):
	    transport_input.append(slab_mat_name[n])
	    transport_input.append('1')
	    transport_input.append(str(slab_length[n]))

	if len(ray_tuples) < 20:
	    spatial_filename = 'spatial_' + str(i) + '.dat'
            #############################################
	    sslab = []
            for d in slab_length:
	        sslab.append(format(d,'.6f'))
	    print ('dist, mats ',  sslab, slab_mat_name)
	    print ('---')
            #############################################
	else:
	    spatial_filename = 'spatial.dat'

	# ToDo: refactor to pass to transport_process
	if i < 50:
	    f = open(spatial_filename, 'w')
	    f.write("\n".join(transport_input))
	    f.close()
	i = i + 1
    ###################################### 
    #
    # Write out the file that will be the input for the transport step
    # one_d_tool.write_spatial_transport_file(slab_length, slab_mat_name)
    one_d_tool.transport_process()
    one_d_tool.response_process()
    outcome = one_d_tool.extract_results()
    # append_csv_file(filename, outcome)

    return
   
if __name__ == '__main__':
    main()
