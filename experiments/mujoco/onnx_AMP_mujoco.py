import argparse
import pickle
import time

import mujoco
import mujoco_viewer
import numpy as np
from scipy.spatial.transform import Rotation as R

from mini_bdx.onnx_infer import OnnxInfer
from mini_bdx.utils.rl_utils import isaac_to_mujoco, mujoco_to_isaac

parser = argparse.ArgumentParser()
parser.add_argument("-o", "--onnx_model_path", type=str, required=True)
parser.add_argument("--saved_obs", type=str, required=False)
parser.add_argument("--saved_actions", type=str, required=False)
args = parser.parse_args()

if args.saved_obs is not None:
    saved_obs = pickle.loads(open("saved_obs.pkl", "rb").read())
if args.saved_actions is not None:
    saved_actions = pickle.loads(open("saved_actions.pkl", "rb").read())


# Params
dt = 0.0001
linearVelocityScale = 2.0
angularVelocityScale = 0.25
dof_pos_scale = 1.0
dof_vel_scale = 0.05
action_clip = (-1, 1)
obs_clip = (-5, 5)
action_scale = 1.0


# mujoco_init_pos = np.array(
#     [
#         # right_leg
#         -0.014,
#         0.08,
#         0.53,
#         -1.32,
#         # -1.52,
#         0.91,
#         # left leg
#         0.013,
#         0.077,
#         0.59,
#         -1.33,
#         # -1.53,
#         0.86,
#         # head
#         -0.17,
#         -0.17,
#         0.0,
#         0.0,
#         0.0,
#     ]
# )

# Higher
mujoco_init_pos = np.array(
    [
        -0.03624976502864328,
        -0.030487325306535976,
        0.2680420987834846,
        -0.8597523079921773,
        0.5923669552154145,
        -0.03626380039794039,
        0.05183131941573333,
        0.21064045281267854,
        -0.7046338428321195,
        0.4946507135822463,
        -0.17453292519943295,
        -0.17453292519943295,
        0,
        0,
        0,
    ]
)


isaac_init_pos = np.array(mujoco_to_isaac(mujoco_init_pos))


model = mujoco.MjModel.from_xml_path("../../mini_bdx/robots/bdx/scene.xml")
model.opt.timestep = dt
data = mujoco.MjData(model)
mujoco.mj_step(model, data)
viewer = mujoco_viewer.MujocoViewer(model, data)
# model.opt.gravity[:] = [0, 0, 0]  # no gravity

policy = OnnxInfer(args.onnx_model_path)


class ImuDelaySimulator:
    def __init__(self, delay_ms):
        self.delay_ms = delay_ms
        self.rot = []
        self.ang_rot = []
        self.t0 = None

    def push(self, rot, ang_rot, t):
        self.rot.append(rot)
        self.ang_rot.append(ang_rot)
        if self.t0 is None:
            self.t0 = t

    def get(self):
        if time.time() - self.t0 < self.delay_ms / 1000:
            return [0, 0, 0, 0], [0, 0, 0]
        rot = self.rot.pop(0)
        ang_rot = self.ang_rot.pop(0)

        return rot, ang_rot


def get_obs(data, isaac_action, commands, imu_delay_simulator: ImuDelaySimulator):

    base_lin_vel = (
        data.sensor("linear-velocity").data.astype(np.double) * linearVelocityScale
    )

    base_quat = data.qpos[3 : 3 + 4].copy()
    base_quat = [base_quat[1], base_quat[2], base_quat[3], base_quat[0]]

    # Remove yaw component
    rot_euler = R.from_quat(base_quat).as_euler("xyz", degrees=False)
    rot_euler[2] = 0
    base_quat = R.from_euler("xyz", rot_euler, degrees=False).as_quat()

    base_ang_vel = (
        data.sensor("angular-velocity").data.astype(np.double) * angularVelocityScale
    )

    mujoco_dof_pos = data.qpos[7 : 7 + 15].copy()
    isaac_dof_pos = mujoco_to_isaac(mujoco_dof_pos)

    isaac_dof_pos_scaled = (isaac_dof_pos - isaac_init_pos) * dof_pos_scale

    mujoco_dof_vel = data.qvel[6 : 6 + 15].copy()
    isaac_dof_vel = mujoco_to_isaac(mujoco_dof_vel)
    isaac_dof_vel_scaled = list(np.array(isaac_dof_vel) * dof_vel_scale)

    imu_delay_simulator.push(base_quat, base_ang_vel, time.time())
    base_quat, base_ang_vel = imu_delay_simulator.get()

    obs = np.concatenate(
        [
            base_quat,
            # base_lin_vel,
            base_ang_vel,
            isaac_dof_pos_scaled,
            isaac_dof_vel_scaled,
            isaac_action,
            commands,
        ]
    )

    return obs


prev_isaac_action = np.zeros(15)
commands = [0.0, 0.0, 0.0]
# prev = time.time()
# last_control = time.time()
prev = data.time
last_control = data.time
control_freq = 30  # hz
i = 0
# data.qpos[3 : 3 + 4] = [1, 0, 0.08, 0]

init_rot = [0, -0.1, 0]
init_rot = [0, 0, 0]
init_quat = R.from_euler("xyz", init_rot, degrees=False).as_quat()
data.qpos[3 : 3 + 4] = init_quat
# data.qpos[3 : 3 + 4] = [init_quat[3], init_quat[1], init_quat[2], init_quat[0]]
data.qpos[3 : 3 + 4] = [1, 0, 0.08, 0]


data.qpos[7 : 7 + 15] = mujoco_init_pos
data.ctrl[:] = mujoco_init_pos

mujoco_saved_obs = []
mujoco_saved_actions = []
command_value = []
imu_delay_simulator = ImuDelaySimulator(1)
try:
    start = time.time()
    while True:
        # t = time.time()
        t = data.time
        if t - last_control >= 1 / control_freq:

            isaac_obs = get_obs(data, prev_isaac_action, commands, imu_delay_simulator)
            mujoco_saved_obs.append(isaac_obs)

            if args.saved_obs is not None:
                isaac_obs = saved_obs[i]  # works with saved obs

            isaac_obs = np.clip(isaac_obs, obs_clip[0], obs_clip[1])

            isaac_action = policy.infer(isaac_obs)
            if args.saved_actions is not None:
                isaac_action = saved_actions[i][0]
            isaac_action = np.clip(isaac_action, action_clip[0], action_clip[1])
            prev_isaac_action = isaac_action.copy()
            isaac_action = isaac_init_pos + isaac_action

            mujoco_action = isaac_to_mujoco(isaac_action)

            last_control = t
            i += 1

            data.ctrl[:] = mujoco_action.copy()
            # data.ctrl[:] = mujoco_init_pos
            # euler_rot = [np.sin(2 * np.pi * 0.5 * t), 0, 0]
            # quat = R.from_euler("xyz", euler_rot, degrees=False).as_quat()
            # data.qpos[3 : 3 + 4] = quat
            mujoco_saved_actions.append(mujoco_action)

            command_value.append([data.ctrl.copy(), data.qpos[7:].copy()])
        mujoco.mj_step(model, data, 50)

        viewer.render()
        prev = t
except KeyboardInterrupt:
    data = {
        "config": {},
        "mujoco": command_value,
    }
    pickle.dump(data, open("mujoco_command_value.pkl", "wb"))
    pickle.dump(mujoco_saved_obs, open("mujoco_saved_obs.pkl", "wb"))
    pickle.dump(mujoco_saved_actions, open("mujoco_saved_actions.pkl", "wb"))
