# Pure math module, imported by laptop_fusion.py. It does no networking.
# (It used to import MicroPython's `network`, which doesn't exist on the
#  laptop and makes `import` fail there.)
import math
import numpy as np

class ackermann_simple_ekf:
    def __init__(self, wheel_radius, distance_between_wheels, initial_position_uncertainty, initial_heading_uncertainty, sensor_uncertainty, initial_left_encoder_value, initial_right_encoder_value, initial_x, initial_y, initial_theta):

        # System Characteristics
        self._wheel_radius = wheel_radius
        self._distance_between_wheels = distance_between_wheels
        self._R = np.array([ [initial_position_uncertainty, 0, 0], 
                    [0, initial_position_uncertainty, 0],
                    [0, 0, initial_heading_uncertainty]])
        self._Q = sensor_uncertainty
        self._C = np.array([0, 0, 1])
        self._Epsilon = np.array([[1e-3, 0.0,  0.0],
                         [0.0,  1e-3, 0.0],
                         [0.0,  0.0,  1e-2]])
        self._K_gain = np.array([0.0, 0.0, 0.0])

        # State
        self._x = initial_x
        self._y = initial_y
        self._theta = initial_theta

        # Input
        self._l = initial_left_encoder_value
        self._r = initial_right_encoder_value

    # -------- Helpers --------
    def wrap_pi(self, a):
        return (a + math.pi) % (2.0 * math.pi) - math.pi
        
    def get_uncertainty(self):
        return self._Epsilon

    def predict(self, left_encoder, right_encoder):
        dl = left_encoder - self._l
        dr = right_encoder - self._r
        self._l, self._r = left_encoder, right_encoder
            
        ds_l = dl * 2.0 * math.pi * self._wheel_radius
        ds_r = dr * 2.0 * math.pi * self._wheel_radius
        ds = 0.5 * (ds_l + ds_r)
        dtheta_enc = (ds_r - ds_l) / self._distance_between_wheels
    
        # ---- Prediction ----
        th_mid = self.wrap_pi(self._theta + 0.5 * dtheta_enc)
        
        x_pred = self._x + ds * math.cos(th_mid)
        y_pred = self._y + ds * math.sin(th_mid)
        th_pred = self.wrap_pi(self._theta + dtheta_enc)
        
        F02 = -ds * math.sin(th_mid)
        F12 = ds * math.cos(th_mid)

        P_old = np.array([row[:] for row in self._Epsilon])
        
        self._Epsilon[0][0] = P_old[0][0] + P_old[0][2]*F02+F02*(P_old[2][0]+P_old[2][2]*F02) + self._R[0][0]
        self._Epsilon[1][0] = P_old[1][0] + P_old[1][2]*F02+F12*(P_old[2][0]+P_old[2][2]*F02)
        self._Epsilon[2][0] = P_old[2][0] + P_old[2][2]*F02
        
        self._Epsilon[0][1] = P_old[0][1] + P_old[0][2]*F12+F02*(P_old[2][1]+P_old[2][2]*F12)
        self._Epsilon[1][1] = P_old[1][1] + P_old[1][2]*F12+F12*(P_old[2][1]+P_old[2][2]*F12) + self._R[1][1]
        self._Epsilon[2][1] = P_old[2][1] + P_old[2][2]*F12
        
        self._Epsilon[0][2] = P_old[0][2] + P_old[2][2]*F02
        self._Epsilon[1][2] = P_old[1][2] + P_old[2][2]*F12
        self._Epsilon[2][2] = P_old[2][2] + self._R[2][2]

        self._x = x_pred
        self._y = y_pred
        self._theta = th_pred

        return self._x, self._y, self._theta

    def correct(self, x_pred, y_pred, th_pred, measurement):
        innov = self.wrap_pi(measurement - self._theta)
            
        constant = 1/(self._Epsilon[2][2] + self._Q)
        self._K_gain[0] = self._Epsilon[0][2]*constant
        self._K_gain[1] = self._Epsilon[1][2]*constant
        self._K_gain[2] = self._Epsilon[2][2]*constant
        
        # self._x = x_pred + self._K_gain[0]*innov # make no measurements on x
        # self._y = y_pred + self._K_gain[1]*innov # make no measurements on y
        # self._theta = self.wrap_pi(th_pred + self._K_gain[2]*innov) # only makes measurements on theta

        self._x = self._x + self._K_gain[0]*innov # make no measurements on x
        self._y = self._y + self._K_gain[1]*innov # make no measurements on y
        self._theta = self.wrap_pi(self._theta + self._K_gain[2]*innov) # only makes measurements on theta

        P_old = np.array([row[:] for row in self._Epsilon])

        self._Epsilon[0][0] = P_old[0][0] - P_old[2][0]*self._K_gain[0]
        self._Epsilon[1][0] = P_old[1][0] - P_old[2][0]*self._K_gain[1]
        self._Epsilon[2][0] = P_old[2][0] - P_old[2][0]*self._K_gain[2]
        
        self._Epsilon[0][1] = P_old[0][1] - P_old[2][1]*self._K_gain[0]
        self._Epsilon[1][1] = P_old[1][1] - P_old[2][1]*self._K_gain[1]
        self._Epsilon[2][1] = P_old[2][1] - P_old[2][1]*self._K_gain[2]
        
        self._Epsilon[0][2] = P_old[0][2] - P_old[2][2]*self._K_gain[0]
        self._Epsilon[1][2] = P_old[1][2] - P_old[2][2]*self._K_gain[1]
        self._Epsilon[2][2] = P_old[2][2] - P_old[2][2]*self._K_gain[2]

        return self._x, self._y, self._theta
    
    def camera_correction(self, x_pred, y_pred, th_pred, AprilTag_x, AprilTag_z, AprilTag_th):
        innov = self.wrap_pi(AprilTag_th - self._theta)

        # Measurement as a column vector [x, y, (theta unused)]. This was a
        # 3x3 diagonal matrix, which numpy broadcast so the y correction
        # used the wrong column: y was pulled toward 0, not the tag's y.
        z_matrix = np.array([[AprilTag_x],
                             [AprilTag_z],
                             [0.0]])

        camera_Q = np.array([[0.001, 0, 0],
                    [0, 0.001, 0],
                    [0, 0, 0.1]])
        
        camera_C = np.array([[1, 0, 0],
                    [0, 1, 0],
                    [0, 0, 0]])
        
        camera_C_transposed = camera_C.T
        
        inverse = np.linalg.inv(camera_C@self._Epsilon@camera_C_transposed + camera_Q)

        camera_K = self._Epsilon@camera_C_transposed@inverse

        state = np.array([[self._x],
                          [self._y],
                          [self._theta],])
        
        state = state + camera_K@(z_matrix - camera_C@state)

        identity_3 = np.identity(3)

        self._Epsilon = (identity_3 - camera_K@camera_C)@self._Epsilon
        
        self._x = float(state[0][0])
        self._y = float(state[1][0])
        self._theta = self.wrap_pi(float(state[2][0]))

        return self._x, self._y, self._theta