import mujoco.viewer

import time
import mujoco
import argparse
import pickle
import numpy as np

from mini_bdx.utils.mujoco_utils import check_contact

from mini_bdx_runtime.onnx_infer import OnnxInfer

from mini_bdx_runtime.rl_utils import (
    # action_to_pd_targets,
    isaac_to_mujoco,
    mujoco_to_isaac,
    mujoco_joints_order,
    isaac_joints_order,
)


parser = argparse.ArgumentParser()
parser.add_argument("-o", "--onnx_model_path", type=str, required=True)
parser.add_argument("-k", action="store_true", default=False)
# parser.add_argument("--rma", action="store_true", default=False)
# parser.add_argument("--awd", action="store_true", default=False)
# parser.add_argument("--adaptation_module_path", type=str, required=False)
parser.add_argument("--replay_obs", type=str, required=False, default=None)
args = parser.parse_args()

if args.k:
    import pygame

    pygame.init()
    # open a blank pygame window
    screen = pygame.display.set_mode((100, 100))
    pygame.display.set_caption("Press arrow keys to move robot")

if args.replay_obs is not None:
    with open(args.replay_obs, "rb") as f:
        replay_obs = pickle.load(f)
        replay_obs = np.array(replay_obs)


def pd_control(target_q, q, kp, target_dq, dq, kd):
    """Calculates torques from position commands"""
    return (target_q - q) * kp + (target_dq - dq) * kd


def quat_rotate_inverse(q, v):
    q = np.array(q)
    v = np.array(v)

    q_w = q[-1]
    q_vec = q[:3]

    a = v * (2.0 * q_w**2 - 1.0)
    b = np.cross(q_vec, v) * q_w * 2.0
    c = q_vec * (np.dot(q_vec, v)) * 2.0

    return a - b + c


def get_obs(data, prev_isaac_action, commands):
    base_quat = data.qpos[3 : 3 + 4].copy()
    base_quat = [base_quat[1], base_quat[2], base_quat[3], base_quat[0]]

    mujoco_dof_pos = data.qpos[7 : 7 + 16].copy()
    isaac_dof_pos = mujoco_to_isaac(mujoco_dof_pos)

    mujoco_dof_vel = data.qvel[6 : 6 + 16].copy()
    isaac_dof_vel = mujoco_to_isaac(mujoco_dof_vel)

    projected_gravity = quat_rotate_inverse(base_quat, [0, 0, -1])
    feet_contacts = get_feet_contact()

    obs = np.concatenate(
        [
            projected_gravity,
            isaac_dof_pos,
            isaac_dof_vel,
            feet_contacts,
            prev_isaac_action,
            commands,
        ]
    )

    return obs


def get_feet_contact():
    left_contact = check_contact(data, model, "foot_assembly", "floor")
    right_contact = check_contact(data, model, "foot_assembly_2", "floor")
    return [left_contact, right_contact]


isaac_init_pos = np.array(
    [
        0.002,
        0.053,
        -0.63,
        1.368,
        -0.784,
        0.002,
        0,
        0,
        0,
        0,
        0,
        -0.003,
        -0.065,
        0.635,
        1.379,
        -0.796,
    ]
)

mujoco_init_pos = np.array(isaac_to_mujoco(isaac_init_pos))

model = mujoco.MjModel.from_xml_path(
    "/home/antoine/MISC/mini_BDX/mini_bdx/robots/open_duck_mini_v2/scene.xml"
)
model.opt.timestep = 0.01
# model.opt.timestep = 1 / 60  # /2 substeps ?
data = mujoco.MjData(model)
# mujoco.mj_step(model, data)
control_decimation = 1

data.qpos[3 : 3 + 4] = [1, 0, 0.0, 0]
data.qpos[7 : 7 + 16] = mujoco_init_pos
data.ctrl[:16] = mujoco_init_pos

policy = OnnxInfer(args.onnx_model_path, awd=True)

commands = [0.3, 0.0, 0.0]

# define context variables
prev_isaac_action = np.zeros(16)
target_dof_pos = mujoco_init_pos.copy()
action_scale = 1

kps = np.array([7] * 16)
kds = np.array([0.1] * 16)

counter = 0
replay_counter = 0
start = time.time()
with mujoco.viewer.launch_passive(
    model, data, show_left_ui=False, show_right_ui=False
) as viewer:
    while True:
        step_start = time.time()

        tau = pd_control(
            target_dof_pos,
            data.qpos[7:].copy(),
            kps,
            np.zeros_like(kds),
            data.qvel[6:].copy(),
            kds,
        )
        data.ctrl[:14] = np.clip(tau, -3, 3)[:14]

        mujoco.mj_step(model, data)
        counter += 1
        get_obs(data, prev_isaac_action, commands)
        if counter % control_decimation == 0 and time.time() - start > 2:
            if args.replay_obs is not None:
                isaac_obs = replay_obs[replay_counter]
                # isaac_action = isaac_obs[-16-3:-3]
                # print(isaac_action)
                replay_counter += 1
            else:
                isaac_obs = get_obs(data, prev_isaac_action, commands)
            isaac_action = policy.infer(isaac_obs)
            isaac_action = np.clip(isaac_action, -1, 1)
            prev_isaac_action = isaac_action.copy()

            mujoco_action = isaac_to_mujoco(isaac_action)
            target_dof_pos = np.array(mujoco_action) * action_scale + mujoco_init_pos

        viewer.sync()
        time_until_next_step = model.opt.timestep - (time.time() - step_start)
        if time_until_next_step > 0:
            time.sleep(time_until_next_step)
