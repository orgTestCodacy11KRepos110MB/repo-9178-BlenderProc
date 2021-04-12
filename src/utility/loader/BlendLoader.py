import os
import re
from typing import List, Union

import bpy

from src.utility.BlenderUtility import collect_all_orphan_datablocks
from src.utility.MeshObjectUtility import MeshObject
from src.utility.Utility import Utility


class BlendLoader:
    known_datablock_names = {"/Camera": "cameras",
                              "/Collection": "collections",
                              "/Image": "images",
                              "/Light": "lights",
                              "/Material": "materials",
                              "/Mesh": "meshes",
                              "/Object": "objects",
                              "/Texture": "textures"}
    valid_datablocks = [collection.lower() for collection in dir(bpy.data) if isinstance(getattr(bpy.data, collection), bpy.types.bpy_prop_collection)]
    valid_object_types = ['mesh', 'curve', 'surface', 'meta', 'font', 'hair', 'pointcloud', 'volume', 'gpencil', 'armature', 'lattice', 'empty', 'light', 'light_probe', 'camera', 'speaker']

    @staticmethod
    def _validate_and_standardizes_configured_list(config_value: Union[list, str], allowed_elements: list, element_name: str) -> list:
        """ Makes sure the given config value is a list, is lower case and only consists of valid elements.

        :param config_value: The configured value that should be standardized and validated.
        :param allowed_elements: A list of valid elements. If one configured element is not contained in this list an exception is thrown.
        :param element_name: How one element is called. Used to create an error message.
        :return: The standardized and validated config value.
        """
        # Make sure we have a list
        if not isinstance(config_value, list):
            config_value = [config_value]
        config_value = [element.lower() for element in config_value]

        # Check that the given elements are valid
        for element in config_value:
            if element not in allowed_elements:
                raise Exception("No such " + element_name + ": " + element)

        return config_value

    @staticmethod
    def load(path: str, obj_types: Union[list, str] = ["mesh", "empty"], name_regrex: str = None, data_blocks: Union[list, str] = "objects", merge_objects: bool = False, merged_object_name: str = 'parent_name') -> List[MeshObject]:
        """
        Loads entities (everything that can be stored in a .blend file's folders, see Blender's documentation for
        bpy.types.ID for more info) that match a name pattern from a specified .blend file's section/datablock.

        :param path: Path to a .blend file.
        :param obj_types: The type of objects to load. This parameter is only relevant when `data_blocks` is set to `"objects"`.
                          Available options are: ['mesh', 'curve', 'hair', 'armature', 'empty', 'light', 'camera']
        :param name_regrex: Regular expression representing a name pattern of entities' (everything that can be stored in a .blend
                         file's folders, see Blender's documentation for bpy.types.ID for more info) names.
        :param data_blocks: The datablock or a list of datablocks which should be loaded from the given .blend file.
                            Available options are: ['armatures', 'cameras', 'curves', 'hairs', 'images', 'lights', 'materials', 'meshes', 'objects', 'textures']
        :param merge_objects: Whether to merge all loaded mesh objects into a single object or not.
        :param merged_object_name: The name of the merged object in case of merge_objects=True. If None of ['filename', 'parent_object'], uses the provided string as filename.
                                   Available options are: ['filename', 'parent_object', '<custom_name>']
        :return: The list of loaded mesh objects.
        """
        # get a path to a .blend file
        path = Utility.resolve_path(path)
        data_blocks = BlendLoader._validate_and_standardizes_configured_list(data_blocks, BlendLoader.valid_datablocks, "data block")
        obj_types = BlendLoader._validate_and_standardizes_configured_list(obj_types, BlendLoader.valid_object_types, "object type")

        # Remember which orphans existed beforehand
        orphans_before = collect_all_orphan_datablocks()

        # Start importing blend file. All objects that should be imported need to be copied from "data_from" to "data_to"
        with bpy.data.libraries.load(path) as (data_from, data_to):
            for data_block in data_blocks:
                # Verify that the given data block is valid
                if hasattr(data_from, data_block):
                    # Find all entities of this data block that match the specified pattern
                    data_to_entities = []
                    for entity_name in getattr(data_from, data_block):
                        if not name_regrex or re.fullmatch(name_regrex, entity_name) is not None:
                            data_to_entities.append(entity_name)
                    # Import them
                    setattr(data_to, data_block, data_to_entities)
                    print("Imported " + str(len(data_to_entities)) + " " + data_block)
                else:
                    raise Exception("No such data block: " + data_block)

        # list for potential parent names - this is required for merging objects
        potential_parent_names = []

        # Go over all imported objects again
        loaded_objects = []
        for data_block in data_blocks:
            # Some adjustments that only affect objects
            if data_block == "objects":
                for obj in getattr(data_to, data_block):
                    # Check that the object type is desired
                    if obj.type.lower() in obj_types:
                        # Link objects to the scene
                        bpy.context.collection.objects.link(obj)
                        loaded_objects.append(obj)

                        # If a camera was imported
                        if obj.type == 'CAMERA':
                            # Make it the active camera in the scene
                            bpy.context.scene.camera = obj

                            # Find the maximum frame number of its key frames
                            max_keyframe = -1
                            if obj.animation_data is not None:
                                fcurves = obj.animation_data.action.fcurves
                                for curve in fcurves:
                                    keyframe_points = curve.keyframe_points
                                    for keyframe in keyframe_points:
                                        max_keyframe = max(max_keyframe, keyframe.co[0])

                            # Set frame_end to the next free keyframe
                            bpy.context.scene.frame_end = max_keyframe + 1

                        # in case of merging: append parent object names
                        if merge_objects is True and obj.parent is None and merged_object_name == 'parent_name':
                            potential_parent_names.append(obj.name)
                    else:
                        # Before removing check if its name is relevant in case of merging
                        if merge_objects and obj.parent is None and merged_object_name == 'parent_name':
                            # we insert at 0 index because sometimes the parent is e.g. a curve
                            potential_parent_names.insert(0, obj.name)
                        # Remove object again if its type is not desired
                        bpy.data.objects.remove(obj, do_unlink=True)
                print("Selected " + str(len(loaded_objects)) + " of the loaded objects by type")
            else:
                loaded_objects.extend(getattr(data_to, data_block))

        # merge objects into one, if desired
        if merge_objects and len(loaded_objects) > 1:
            # retrieve the name of the new object
            if merged_object_name == 'filename':
                parent_name = path.split('/')[-1][:-6]
            elif merged_object_name == 'parent_name':
                if len(potential_parent_names) >= 1:
                    parent_name = potential_parent_names[0]
                else:
                    parent_name = path.split('/')[-1][:-6]
                # sometimes the parent name contains a .001 (or any other int) at the end
                if parent_name[-4] == '.' and parent_name[-3:].isdigit():
                    parent_name = parent_name[:-4]
            else:
                parent_name = merged_object_name
            # create new empty object which acts as parent, and link it to the collection
            parent_obj = bpy.data.objects.new(parent_name, None)
            bpy.context.collection.objects.link(parent_obj)

            # select all relevant objects
            for loaded_object in loaded_objects:
                # objects with a parent will be skipped, as this relationship will otherwise be broken
                # if a parent exists this object should be grandchild of parent_obj (or grandgrand...)
                if loaded_object.parent is not None:
                    continue
                # if the object doesn't have a parent we can set its parent
                loaded_object.parent = parent_obj

        # As some loaded objects were deleted again due to their type, we need also to remove the dependent datablocks that were also loaded and are now orphans
        BlendLoader._purge_added_orphans(orphans_before, data_to)

        if merge_objects and len(loaded_objects) > 1:
            return [bpy.data.objects[parent_name]]

        return loaded_objects

    @staticmethod
    def _purge_added_orphans(orphans_before, data_to):
        """ Removes all orphans that did not exists before loading the blend file.

        :param orphans_before: A dict of sets containing orphans of all kind of datablocks that existed before loading the blend file.
        :param data_to: The list of objects that were loaded on purpose and should not be removed, even when they are orphans.
        """
        purge_orphans = True
        while purge_orphans:
            purge_orphans = False
            orphans_after = collect_all_orphan_datablocks()
            # Go over all datablock types
            for collection_name in orphans_after.keys():
                # Go over all orphans of that type that were added due to this loader
                for orphan in orphans_after[collection_name].difference(orphans_before[collection_name]):
                    # Check whether this orphan was loaded on purpose
                    if orphan not in getattr(data_to, collection_name):
                        # Remove the orphan
                        getattr(bpy.data, collection_name).remove(orphan)
                        # Make sure to run the loop again, so we can detect newly created orphans
                        purge_orphans = True