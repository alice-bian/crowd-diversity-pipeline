bl_info = {
    "name": "Crowd Diversity USD Pipeline",
    "author": "Alice",
    "description": "Batch export rigged garments as USD assets with JSON metadata and fit-check poses.",
    "blender": (2, 80, 0),
    "version": (1, 0, 0),
    "location": "View3D > Sidebar > Crowd Diversity",
    "warning": "",
    "category": "Animation",
}

from . import auto_load

auto_load.init()


def register():
    auto_load.register()


def unregister():
    auto_load.unregister()
