bl_info = {
    "name": "Simple VR Tools (OpenXR) – Presence+",
    "author": "Levente Botos",
    "version": (1, 2, 0),
    "blender": (3, 0, 0),
    "location": "3D Viewport > N-Panel > VR",
    "description": "QoL VR controls with room-scale calibration, ‘teleport’ locomotion, snap-to-ground, camera align, and presentation mode for Blender’s OpenXR.",
    "category": "3D View",
}

import bpy
import math
from mathutils import Vector
from bpy.props import BoolProperty, FloatProperty

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def xr_supported():
    return hasattr(bpy.ops.wm, "xr_session_toggle")


def get_xr_settings():
    return getattr(bpy.context.window_manager, "xr_session_settings", None)


def xr_session_running():
    state = getattr(bpy.context.window_manager, "xr_session_state", None)
    return bool(state and state.is_running)


def ensure_units_meters(scene: bpy.types.Scene):
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1.0  # 1 BU = 1 meter


def set_base_pose(location: Vector = None, angle_z: float = None, use_custom=True):
    settings = get_xr_settings()
    if not settings:
        raise RuntimeError("XR settings unavailable.")
    if use_custom:
        settings.base_pose_type = 'CUSTOM'
    if location is not None:
        settings.base_pose_location = location
    if angle_z is not None:
        settings.base_pose_angle = angle_z


def scene_ground_z(context):
    # Try to infer a sensible ground height: priority
    # 1) Any object named like ground/floor (case-insensitive) bounding-box min Z
    # 2) World origin Z=0
    candidates = [o for o in context.scene.objects if o.type in {'MESH', 'CURVE', 'SURFACE', 'FONT'}]
    prioritized = []
    for o in candidates:
        n = o.name.lower()
        if any(key in n for key in ("ground", "floor", "terrain")):
            prioritized.append(o)
    if prioritized:
        o = prioritized[0]
        coords = [o.matrix_world @ Vector(v) for v in o.bound_box]
        zmin = min(c.z for c in coords)
        return zmin
    return 0.0


def object_bottom_z(obj):
    coords = [obj.matrix_world @ Vector(v) for v in obj.bound_box]
    return min(c.z for c in coords)


# --------------------------------------------------
# Properties (Session-scoped)
# --------------------------------------------------

class VRPresenceProps(bpy.types.PropertyGroup):
    player_eye_height: FloatProperty(
        name="Eye Height",
        description="Approximate viewer eye height above the floor (meters)",
        default=1.7, min=1.2, max=2.2,
    )
    teleport_step_height: FloatProperty(
        name="Step Height",
        description="Vertical offset when resolving ground snapping (meters)",
        default=0.05, min=0.0, max=0.5,
    )
    teleport_clearance: FloatProperty(
        name="Teleport Clearance",
        description="Raise the base pose slightly to avoid clipping (meters)",
        default=0.02, min=0.0, max=0.2,
    )
    presentation_mode: BoolProperty(
        name="Presentation Mode",
        description="Hide selection/overlays for clean VR viewing",
        default=False,
    )


# --------------------------------------------------
# Operators
# --------------------------------------------------

class VR_OT_session_toggle(bpy.types.Operator):
    bl_idname = "vr.session_toggle"
    bl_label = "Start / Stop VR"

    def execute(self, context):
        if not xr_supported():
            self.report({'ERROR'}, "This Blender build has no OpenXR support.")
            return {'CANCELLED'}
        try:
            bpy.ops.wm.xr_session_toggle()
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to toggle XR session: {e}")
            return {'CANCELLED'}


class VR_OT_recenter(bpy.types.Operator):
    bl_idname = "vr.recenter"
    bl_label = "Recenter"

    def execute(self, context):
        if not xr_supported():
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        try:
            if hasattr(bpy.ops.wm, 'xr_recenter'):
                bpy.ops.wm.xr_recenter()
            elif hasattr(bpy.ops.wm, 'xr_viewer_pose_reset'):
                bpy.ops.wm, 'xr_viewer_pose_reset()'  # some old builds
                bpy.ops.wm.xr_viewer_pose_reset()
            else:
                raise RuntimeError("No recenter operator available.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to recenter: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VR_OT_set_active_camera(bpy.types.Operator):
    bl_idname = "vr.set_active_camera_from_hmd"
    bl_label = "Set Active Camera from HMD"

    def execute(self, context):
        if not xr_supported():
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        try:
            if hasattr(bpy.ops.wm, 'xr_camera_set'):
                bpy.ops.wm.xr_camera_set()
            else:
                raise RuntimeError("wm.xr_camera_set not available.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to set camera: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VR_OT_focus_selected(bpy.types.Operator):
    bl_idname = "vr.view_selected"
    bl_label = "View Selected in VR"

    def execute(self, context):
        if not xr_supported():
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        obj = context.active_object
        if not obj:
            self.report({'WARNING'}, "No active object.")
            return {'CANCELLED'}
        try:
            set_base_pose(location=obj.matrix_world.translation, use_custom=True)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to focus: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VR_OT_calibrate_room(bpy.types.Operator):
    """Set sane defaults for scale & floor so stepping into the scene feels right."""
    bl_idname = "vr.calibrate_room"
    bl_label = "Calibrate Room (1m scale)"

    def execute(self, context):
        if not xr_supported():
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        scene = context.scene
        ensure_units_meters(scene)
        s = get_xr_settings()
        if not s:
            self.report({'ERROR'}, "XR settings unavailable.")
            return {'CANCELLED'}
        s.show_floor = True
        s.floor_height = scene_ground_z(context)
        s.base_pose_type = 'CUSTOM'
        # Place viewer at eye-height above floor at world origin
        props = context.window_manager.vr_presence
        eye_z = s.floor_height + props.player_eye_height
        s.base_pose_location = Vector((0.0, 0.0, eye_z))
        s.base_pose_angle = 0.0
        self.report({'INFO'}, f"Units set to meters. Floor={s.floor_height:.2f}m, Eye={eye_z:.2f}m")
        return {'FINISHED'}


class VR_OT_teleport_to_cursor(bpy.types.Operator):
    bl_idname = "vr.teleport_to_cursor"
    bl_label = "Teleport to 3D Cursor"

    def execute(self, context):
        s = get_xr_settings()
        if not (xr_supported() and s):
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        props = context.window_manager.vr_presence
        # Snap the base pose above cursor by eye height
        loc = context.scene.cursor.location.copy()
        loc.z += props.player_eye_height + props.teleport_clearance
        try:
            set_base_pose(location=loc, use_custom=True)
        except Exception as e:
            self.report({'ERROR'}, f"Teleport failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VR_OT_teleport_to_selected(bpy.types.Operator):
    bl_idname = "vr.teleport_to_selected"
    bl_label = "Teleport to Selected"

    def execute(self, context):
        s = get_xr_settings()
        if not (xr_supported() and s):
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        obj = context.active_object
        if not obj:
            self.report({'WARNING'}, "No active object.")
            return {'CANCELLED'}
        props = context.window_manager.vr_presence
        target = obj.matrix_world.translation.copy()
        # Put eyes just above object's base (or pivot if no bound box)
        try:
            z = object_bottom_z(obj)
        except Exception:
            z = target.z
        target.z = max(z, target.z) + props.player_eye_height + props.teleport_clearance
        try:
            set_base_pose(location=target, use_custom=True)
        except Exception as e:
            self.report({'ERROR'}, f"Teleport failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VR_OT_snap_to_ground(bpy.types.Operator):
    bl_idname = "vr.snap_to_ground"
    bl_label = "Snap to Ground"
    bl_description = "Raycast down from current base pose to stand on nearest surface"

    max_search: FloatProperty(name="Max Search (m)", default=20.0, min=0.1, max=200.0)

    def execute(self, context):
        s = get_xr_settings()
        if not (xr_supported() and s):
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        depsgraph = context.evaluated_depsgraph_get()
        origin = s.base_pose_location.copy()
        ray_origin = origin + Vector((0, 0, 0.5))
        ray_dir = Vector((0, 0, -1))
        hit_loc = None
        # Try scene ray cast against all evaluated objects
        for obj in context.scene.objects:
            if obj.type != 'MESH':
                continue
            eval_obj = obj.evaluated_get(depsgraph)
            success, location, normal, index = eval_obj.ray_cast(ray_origin, ray_dir, distance=self.max_search)
            if success:
                if (hit_loc is None) or (location.z > hit_loc.z):  # choose highest hit below
                    hit_loc = location
        props = context.window_manager.vr_presence
        if hit_loc is not None:
            new_loc = hit_loc.copy()
            new_loc.z += props.player_eye_height + props.teleport_clearance
            try:
                set_base_pose(location=new_loc, use_custom=True)
            except Exception as e:
                self.report({'ERROR'}, f"Snap failed: {e}")
                return {'CANCELLED'}
            return {'FINISHED'}
        else:
            # Fallback to floor height if set
            new_loc = s.base_pose_location.copy()
            new_loc.z = s.floor_height + props.player_eye_height + props.teleport_clearance
            set_base_pose(location=new_loc, use_custom=True)
            self.report({'INFO'}, "No ground hit; used floor height.")
            return {'FINISHED'}


class VR_OT_presentation_mode(bpy.types.Operator):
    bl_idname = "vr.presentation_mode_toggle"
    bl_label = "Toggle Presentation Mode"

    def execute(self, context):
        s = get_xr_settings()
        if not s:
            self.report({'ERROR'}, "XR settings unavailable.")
            return {'CANCELLED'}
        props = context.window_manager.vr_presence
        props.presentation_mode = not props.presentation_mode
        if props.presentation_mode:
            s.show_selection = False
            s.show_object_extras = False
            s.show_controllers = True
        else:
            s.show_selection = True
            s.show_object_extras = True
        return {'FINISHED'}


# --------------------------------------------------
# UI Panel
# --------------------------------------------------

class VR_PT_tools(bpy.types.Panel):
    bl_label = "VR"
    bl_idname = "VR_PT_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'VR'

    def draw(self, context):
        layout = self.layout
        s = get_xr_settings()
        props = context.window_manager.vr_presence

        # Session
        col = layout.column(align=True)
        col.label(text="Session")
        row = col.row(align=True)
        row.operator(VR_OT_session_toggle.bl_idname, text=("Stop VR" if xr_session_running() else "Start VR"), icon='VIEW_CAMERA')
        row.operator(VR_OT_recenter.bl_idname, text="Recenter", icon='CURSOR')
        col.operator(VR_OT_presentation_mode.bl_idname, text=("Presentation: ON" if props.presentation_mode else "Presentation: OFF"), icon='HIDE_OFF')

        # Calibration
        col = layout.column(align=True)
        col.label(text="Calibration & Scale")
        col.prop(props, "player_eye_height")
        col.operator(VR_OT_calibrate_room.bl_idname, icon='EVENT_SPACEKEY')

        if not s:
            box = layout.box()
            box.label(text="OpenXR not available in this build.", icon='ERROR')
            return

        # Locomotion
        col = layout.column(align=True)
        col.label(text="Teleport Locomotion")
        col.prop(props, "teleport_clearance")
        col.operator(VR_OT_teleport_to_cursor.bl_idname, icon='ORIENTATION_CURSOR')
        col.operator(VR_OT_teleport_to_selected.bl_idname, icon='RESTRICT_SELECT_OFF')
        col.operator(VR_OT_snap_to_ground.bl_idname, icon='SNAP_ON')

        # Display
        col = layout.column(align=True)
        col.label(text="Display")
        col.prop(s, "show_floor", text="Show Floor")
        col.prop(s, "floor_height", text="Floor Height (m)")
        col.prop(s, "show_object_extras", text="Show Object Extras")
        col.prop(s, "show_selection", text="Show Selection Outlines")
        col.prop(s, "show_controllers", text="Show Controllers")
        if hasattr(s, 'controller_draw_style'):
            col.prop(s, "controller_draw_style", text="Controller Style")

        # Base Pose (advanced)
        col = layout.column(align=True)
        col.label(text="Base Pose (Advanced)")
        col.prop(s, "base_pose_type", text="Type")
        col.prop(s, "base_pose_object", text="Object")
        col.prop(s, "base_pose_location", text="Location")
        col.prop(s, "base_pose_angle", text="Angle (Z)")


# --------------------------------------------------
# Registration
# --------------------------------------------------

classes = (
    VRPresenceProps,
    VR_OT_session_toggle,
    VR_OT_recenter,
    VR_OT_set_active_camera,
    VR_OT_focus_selected,
    VR_OT_calibrate_room,
    VR_OT_teleport_to_cursor,
    VR_OT_teleport_to_selected,
    VR_OT_snap_to_ground,
    VR_OT_presentation_mode,
    VR_PT_tools,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.vr_presence = bpy.props.PointerProperty(type=VRPresenceProps)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.WindowManager, 'vr_presence'):
        del bpy.types.WindowManager.vr_presence


if __name__ == "__main__":
    register()
bl_info = {
    "name": "Simple VR Tools (OpenXR) – Presence+",
    "author": "ChatGPT",
    "version": (1, 2, 0),
    "blender": (3, 0, 0),
    "location": "3D Viewport > N-Panel > VR",
    "description": "QoL VR controls with room-scale calibration, ‘teleport’ locomotion, snap-to-ground, camera align, and presentation mode for Blender’s OpenXR.",
    "category": "3D View",
}

import bpy
import math
from mathutils import Vector
from bpy.props import BoolProperty, FloatProperty

# --------------------------------------------------
# Helpers
# --------------------------------------------------

def xr_supported():
    return hasattr(bpy.ops.wm, "xr_session_toggle")


def get_xr_settings():
    return getattr(bpy.context.window_manager, "xr_session_settings", None)


def xr_session_running():
    state = getattr(bpy.context.window_manager, "xr_session_state", None)
    return bool(state and state.is_running)


def ensure_units_meters(scene: bpy.types.Scene):
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1.0  # 1 BU = 1 meter


def set_base_pose(location: Vector = None, angle_z: float = None, use_custom=True):
    settings = get_xr_settings()
    if not settings:
        raise RuntimeError("XR settings unavailable.")
    if use_custom:
        settings.base_pose_type = 'CUSTOM'
    if location is not None:
        settings.base_pose_location = location
    if angle_z is not None:
        settings.base_pose_angle = angle_z


def scene_ground_z(context):
    # Try to infer a sensible ground height: priority
    # 1) Any object named like ground/floor (case-insensitive) bounding-box min Z
    # 2) World origin Z=0
    candidates = [o for o in context.scene.objects if o.type in {'MESH', 'CURVE', 'SURFACE', 'FONT'}]
    prioritized = []
    for o in candidates:
        n = o.name.lower()
        if any(key in n for key in ("ground", "floor", "terrain")):
            prioritized.append(o)
    if prioritized:
        o = prioritized[0]
        coords = [o.matrix_world @ Vector(v) for v in o.bound_box]
        zmin = min(c.z for c in coords)
        return zmin
    return 0.0


def object_bottom_z(obj):
    coords = [obj.matrix_world @ Vector(v) for v in obj.bound_box]
    return min(c.z for c in coords)


# --------------------------------------------------
# Properties (Session-scoped)
# --------------------------------------------------

class VRPresenceProps(bpy.types.PropertyGroup):
    player_eye_height: FloatProperty(
        name="Eye Height",
        description="Approximate viewer eye height above the floor (meters)",
        default=1.7, min=1.2, max=2.2,
    )
    teleport_step_height: FloatProperty(
        name="Step Height",
        description="Vertical offset when resolving ground snapping (meters)",
        default=0.05, min=0.0, max=0.5,
    )
    teleport_clearance: FloatProperty(
        name="Teleport Clearance",
        description="Raise the base pose slightly to avoid clipping (meters)",
        default=0.02, min=0.0, max=0.2,
    )
    presentation_mode: BoolProperty(
        name="Presentation Mode",
        description="Hide selection/overlays for clean VR viewing",
        default=False,
    )


# --------------------------------------------------
# Operators
# --------------------------------------------------

class VR_OT_session_toggle(bpy.types.Operator):
    bl_idname = "vr.session_toggle"
    bl_label = "Start / Stop VR"

    def execute(self, context):
        if not xr_supported():
            self.report({'ERROR'}, "This Blender build has no OpenXR support.")
            return {'CANCELLED'}
        try:
            bpy.ops.wm.xr_session_toggle()
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to toggle XR session: {e}")
            return {'CANCELLED'}


class VR_OT_recenter(bpy.types.Operator):
    bl_idname = "vr.recenter"
    bl_label = "Recenter"

    def execute(self, context):
        if not xr_supported():
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        try:
            if hasattr(bpy.ops.wm, 'xr_recenter'):
                bpy.ops.wm.xr_recenter()
            elif hasattr(bpy.ops.wm, 'xr_viewer_pose_reset'):
                bpy.ops.wm, 'xr_viewer_pose_reset()'  # some old builds
                bpy.ops.wm.xr_viewer_pose_reset()
            else:
                raise RuntimeError("No recenter operator available.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to recenter: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VR_OT_set_active_camera(bpy.types.Operator):
    bl_idname = "vr.set_active_camera_from_hmd"
    bl_label = "Set Active Camera from HMD"

    def execute(self, context):
        if not xr_supported():
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        try:
            if hasattr(bpy.ops.wm, 'xr_camera_set'):
                bpy.ops.wm.xr_camera_set()
            else:
                raise RuntimeError("wm.xr_camera_set not available.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to set camera: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VR_OT_focus_selected(bpy.types.Operator):
    bl_idname = "vr.view_selected"
    bl_label = "View Selected in VR"

    def execute(self, context):
        if not xr_supported():
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        obj = context.active_object
        if not obj:
            self.report({'WARNING'}, "No active object.")
            return {'CANCELLED'}
        try:
            set_base_pose(location=obj.matrix_world.translation, use_custom=True)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to focus: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VR_OT_calibrate_room(bpy.types.Operator):
    """Set sane defaults for scale & floor so stepping into the scene feels right."""
    bl_idname = "vr.calibrate_room"
    bl_label = "Calibrate Room (1m scale)"

    def execute(self, context):
        if not xr_supported():
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        scene = context.scene
        ensure_units_meters(scene)
        s = get_xr_settings()
        if not s:
            self.report({'ERROR'}, "XR settings unavailable.")
            return {'CANCELLED'}
        s.show_floor = True
        s.floor_height = scene_ground_z(context)
        s.base_pose_type = 'CUSTOM'
        # Place viewer at eye-height above floor at world origin
        props = context.window_manager.vr_presence
        eye_z = s.floor_height + props.player_eye_height
        s.base_pose_location = Vector((0.0, 0.0, eye_z))
        s.base_pose_angle = 0.0
        self.report({'INFO'}, f"Units set to meters. Floor={s.floor_height:.2f}m, Eye={eye_z:.2f}m")
        return {'FINISHED'}


class VR_OT_teleport_to_cursor(bpy.types.Operator):
    bl_idname = "vr.teleport_to_cursor"
    bl_label = "Teleport to 3D Cursor"

    def execute(self, context):
        s = get_xr_settings()
        if not (xr_supported() and s):
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        props = context.window_manager.vr_presence
        # Snap the base pose above cursor by eye height
        loc = context.scene.cursor.location.copy()
        loc.z += props.player_eye_height + props.teleport_clearance
        try:
            set_base_pose(location=loc, use_custom=True)
        except Exception as e:
            self.report({'ERROR'}, f"Teleport failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VR_OT_teleport_to_selected(bpy.types.Operator):
    bl_idname = "vr.teleport_to_selected"
    bl_label = "Teleport to Selected"

    def execute(self, context):
        s = get_xr_settings()
        if not (xr_supported() and s):
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        obj = context.active_object
        if not obj:
            self.report({'WARNING'}, "No active object.")
            return {'CANCELLED'}
        props = context.window_manager.vr_presence
        target = obj.matrix_world.translation.copy()
        # Put eyes just above object's base (or pivot if no bound box)
        try:
            z = object_bottom_z(obj)
        except Exception:
            z = target.z
        target.z = max(z, target.z) + props.player_eye_height + props.teleport_clearance
        try:
            set_base_pose(location=target, use_custom=True)
        except Exception as e:
            self.report({'ERROR'}, f"Teleport failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VR_OT_snap_to_ground(bpy.types.Operator):
    bl_idname = "vr.snap_to_ground"
    bl_label = "Snap to Ground"
    bl_description = "Raycast down from current base pose to stand on nearest surface"

    max_search: FloatProperty(name="Max Search (m)", default=20.0, min=0.1, max=200.0)

    def execute(self, context):
        s = get_xr_settings()
        if not (xr_supported() and s):
            self.report({'ERROR'}, "OpenXR not available.")
            return {'CANCELLED'}
        depsgraph = context.evaluated_depsgraph_get()
        origin = s.base_pose_location.copy()
        ray_origin = origin + Vector((0, 0, 0.5))
        ray_dir = Vector((0, 0, -1))
        hit_loc = None
        # Try scene ray cast against all evaluated objects
        for obj in context.scene.objects:
            if obj.type != 'MESH':
                continue
            eval_obj = obj.evaluated_get(depsgraph)
            success, location, normal, index = eval_obj.ray_cast(ray_origin, ray_dir, distance=self.max_search)
            if success:
                if (hit_loc is None) or (location.z > hit_loc.z):  # choose highest hit below
                    hit_loc = location
        props = context.window_manager.vr_presence
        if hit_loc is not None:
            new_loc = hit_loc.copy()
            new_loc.z += props.player_eye_height + props.teleport_clearance
            try:
                set_base_pose(location=new_loc, use_custom=True)
            except Exception as e:
                self.report({'ERROR'}, f"Snap failed: {e}")
                return {'CANCELLED'}
            return {'FINISHED'}
        else:
            # Fallback to floor height if set
            new_loc = s.base_pose_location.copy()
            new_loc.z = s.floor_height + props.player_eye_height + props.teleport_clearance
            set_base_pose(location=new_loc, use_custom=True)
            self.report({'INFO'}, "No ground hit; used floor height.")
            return {'FINISHED'}


class VR_OT_presentation_mode(bpy.types.Operator):
    bl_idname = "vr.presentation_mode_toggle"
    bl_label = "Toggle Presentation Mode"

    def execute(self, context):
        s = get_xr_settings()
        if not s:
            self.report({'ERROR'}, "XR settings unavailable.")
            return {'CANCELLED'}
        props = context.window_manager.vr_presence
        props.presentation_mode = not props.presentation_mode
        if props.presentation_mode:
            s.show_selection = False
            s.show_object_extras = False
            s.show_controllers = True
        else:
            s.show_selection = True
            s.show_object_extras = True
        return {'FINISHED'}


# --------------------------------------------------
# UI Panel
# --------------------------------------------------

class VR_PT_tools(bpy.types.Panel):
    bl_label = "VR"
    bl_idname = "VR_PT_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'VR'

    def draw(self, context):
        layout = self.layout
        s = get_xr_settings()
        props = context.window_manager.vr_presence

        # Session
        col = layout.column(align=True)
        col.label(text="Session")
        row = col.row(align=True)
        row.operator(VR_OT_session_toggle.bl_idname, text=("Stop VR" if xr_session_running() else "Start VR"), icon='VIEW_CAMERA')
        row.operator(VR_OT_recenter.bl_idname, text="Recenter", icon='CURSOR')
        col.operator(VR_OT_presentation_mode.bl_idname, text=("Presentation: ON" if props.presentation_mode else "Presentation: OFF"), icon='HIDE_OFF')

        # Calibration
        col = layout.column(align=True)
        col.label(text="Calibration & Scale")
        col.prop(props, "player_eye_height")
        col.operator(VR_OT_calibrate_room.bl_idname, icon='EVENT_SPACEKEY')

        if not s:
            box = layout.box()
            box.label(text="OpenXR not available in this build.", icon='ERROR')
            return

        # Locomotion
        col = layout.column(align=True)
        col.label(text="Teleport Locomotion")
        col.prop(props, "teleport_clearance")
        col.operator(VR_OT_teleport_to_cursor.bl_idname, icon='ORIENTATION_CURSOR')
        col.operator(VR_OT_teleport_to_selected.bl_idname, icon='RESTRICT_SELECT_OFF')
        col.operator(VR_OT_snap_to_ground.bl_idname, icon='SNAP_ON')

        # Display
        col = layout.column(align=True)
        col.label(text="Display")
        col.prop(s, "show_floor", text="Show Floor")
        col.prop(s, "floor_height", text="Floor Height (m)")
        col.prop(s, "show_object_extras", text="Show Object Extras")
        col.prop(s, "show_selection", text="Show Selection Outlines")
        col.prop(s, "show_controllers", text="Show Controllers")
        if hasattr(s, 'controller_draw_style'):
            col.prop(s, "controller_draw_style", text="Controller Style")

        # Base Pose (advanced)
        col = layout.column(align=True)
        col.label(text="Base Pose (Advanced)")
        col.prop(s, "base_pose_type", text="Type")
        col.prop(s, "base_pose_object", text="Object")
        col.prop(s, "base_pose_location", text="Location")
        col.prop(s, "base_pose_angle", text="Angle (Z)")


# --------------------------------------------------
# Registration
# --------------------------------------------------

classes = (
    VRPresenceProps,
    VR_OT_session_toggle,
    VR_OT_recenter,
    VR_OT_set_active_camera,
    VR_OT_focus_selected,
    VR_OT_calibrate_room,
    VR_OT_teleport_to_cursor,
    VR_OT_teleport_to_selected,
    VR_OT_snap_to_ground,
    VR_OT_presentation_mode,
    VR_PT_tools,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.vr_presence = bpy.props.PointerProperty(type=VRPresenceProps)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.WindowManager, 'vr_presence'):
        del bpy.types.WindowManager.vr_presence


if __name__ == "__main__":
    register()
