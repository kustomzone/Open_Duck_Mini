# run sin motion joints, get command vs data
# in mujoco and on real robot

import argparse
import os
import pickle
import time

import mujoco
import mujoco_viewer
import numpy as np
from mini_bdx_runtime.hwi import HWI
from utils import dof_to_id, id_to_dof, mujoco_init_pos

parser = argparse.ArgumentParser()
parser.add_argument("--dof", type=str, required=True)
parser.add_argument("--move_freq", type=float, default=10)
parser.add_argument("--move_amp", type=float, default=0.5)
parser.add_argument("--ctrl_freq", type=float, default=30)
parser.add_argument("--sampling_freq", type=float, default=100)
parser.add_argument("--duration", type=float, default=5)
parser.add_argument("--save_dir", type=str, default="./data")
args = parser.parse_args()

os.makedirs(args.save_dir, exist_ok=True)

dt = 0.001

assert args.dof in id_to_dof.values()


## === Init mujoco ===
# Commented freejoint
model = mujoco.MjModel.from_xml_path("../../mini_bdx/robots/bdx/scene.xml")
model.opt.timestep = dt
data = mujoco.MjData(model)
mujoco.mj_step(model, data)
viewer = mujoco_viewer.MujocoViewer(model, data)
data.qpos = mujoco_init_pos
data.ctrl[:] = mujoco_init_pos
mujoco_command_value = []

## === Init robot ===
hwi = HWI(usb_port="/dev/ttyUSB0")
time.sleep(1)
hwi.turn_on()
# pid = [100, 0, 8]
pid = [1000, 0, 500]
hwi.set_pid_all(pid)
robot_command_value = []


prev = time.time()
last_control = time.time()
last_sample = time.time()
start = time.time()
while True:
    t = time.time()
    dt = t - prev
    if t - last_control > 1 / args.ctrl_freq:
        last_control = t
        target = (
            mujoco_init_pos[dof_to_id[args.dof]]
            + np.sin(args.move_freq * t) * args.move_amp
        )
        data.ctrl[dof_to_id[args.dof]] = target
        hwi.set_position(args.dof, target)
    mujoco.mj_step(model, data, 5)

    if t - last_sample > 1 / args.sampling_freq:
        last_sample = t
        mujoco_command_value.append(
            [
                data.ctrl[dof_to_id[args.dof]].copy(),
                data.qpos[dof_to_id[args.dof]].copy(),
            ]
        )
        robot_command_value.append(
            [
                target,
                hwi.get_present_positions()[dof_to_id[args.dof]],
            ]
        )

        mujoco_dof_vel = np.around(data.qvel[dof_to_id[args.dof]], 3)
        robot_dof_vel = np.around(hwi.get_present_velocities()[dof_to_id[args.dof]], 3)
        print(mujoco_dof_vel, robot_dof_vel)

    if t - start > args.duration:
        break

    viewer.render()
    prev = t

path = os.path.join(args.save_dir, f"{args.dof}.pkl")
data_dict = {
    "config": {
        "dof": args.dof,
        "move_freq": args.move_freq,
        "move_amp": args.move_amp,
        "ctrl_freq": args.ctrl_freq,
        "sampling_freq": args.sampling_freq,
        "duration": args.duration,
    },
    "mujoco": mujoco_command_value,
    "robot": robot_command_value,
}
pickle.dump(data_dict, open(path, "wb"))
print("saved to", path)
