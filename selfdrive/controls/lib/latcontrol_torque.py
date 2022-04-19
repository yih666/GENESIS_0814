import math
from common.numpy_fast import interp
from selfdrive.controls.lib.latcontrol_pid import ERROR_RATE_FRAME
from selfdrive.controls.lib.pid import PIDController
from selfdrive.controls.lib.latcontrol import LatControl, MIN_STEER_SPEED
from cereal import log
from selfdrive.ntune import nTune

CURVATURE_SCALE = 200
JERK_THRESHOLD = 0.2


class LatControlTorque(LatControl):
  def __init__(self, CP, CI):
    super().__init__(CP, CI)
    self.pid = PIDController(CP.lateralTuning.torque.kp, CP.lateralTuning.torque.ki,
                            k_d=CP.lateralTuning.torque.kd,
                            k_f=CP.lateralTuning.torque.kf, pos_limit=1.0, neg_limit=-1.0)
    self.get_steer_feedforward = CI.get_steer_feedforward_function()
    self.steer_max = 1.0
    self.pid.pos_limit = self.steer_max
    self.pid.neg_limit = -self.steer_max
    self.use_steering_angle = CP.lateralTuning.torque.useSteeringAngle
    self.friction = CP.lateralTuning.torque.friction
    self.errors = []
    self.tune = nTune(CP, self)

  def reset(self):
    super().reset()
    self.pid.reset()

  def update(self, active, CS, CP, VM, params, last_actuators, desired_curvature, desired_curvature_rate, llk):
    self.tune.check()
    pid_log = log.ControlsState.LateralTorqueState.new_message()

    if CS.vEgo < MIN_STEER_SPEED or not active:
      output_torque = 0.0
      pid_log.active = False
      self.pid.reset()
    else:
      if self.use_steering_angle:
        actual_curvature = -VM.calc_curvature(math.radians(CS.steeringAngleDeg - params.angleOffsetDeg), CS.vEgo, params.roll)
      else:
        actual_curvature = llk.angularVelocityCalibrated.value[2] / CS.vEgo
      desired_lateral_accel = desired_curvature * CS.vEgo**2
      desired_lateral_jerk = desired_curvature_rate * CS.vEgo**2
      actual_lateral_accel = actual_curvature * CS.vEgo**2

      setpoint = desired_lateral_accel + CURVATURE_SCALE * desired_curvature
      measurement = actual_lateral_accel + CURVATURE_SCALE * actual_curvature
      error = setpoint - measurement
      pid_log.error = error

      error_rate = 0
      if len(self.errors) >= ERROR_RATE_FRAME:
        error_rate = (error - self.errors[-ERROR_RATE_FRAME]) / ERROR_RATE_FRAME

      self.errors.append(float(error))
      while len(self.errors) > ERROR_RATE_FRAME:
        self.errors.pop(0)

      ff = desired_lateral_accel - params.roll * 9.81
      output_torque = self.pid.update(error,
                                      override=CS.steeringPressed, feedforward=ff,
                                      speed=CS.vEgo)

      friction_compensation = interp(desired_lateral_jerk, [-JERK_THRESHOLD, JERK_THRESHOLD], [-self.friction, self.friction])
      output_torque += friction_compensation

      pid_log.active = True
      pid_log.p = self.pid.p
      pid_log.i = self.pid.i
      pid_log.d = self.pid.d
      pid_log.f = self.pid.f
      pid_log.output = -output_torque
      pid_log.saturated = self._check_saturation(self.steer_max - abs(output_torque) < 1e-3, CS)

    #TODO left is positive in this convention
    return -output_torque, 0.0, pid_log
