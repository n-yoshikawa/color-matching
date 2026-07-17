import math

import sys
sys.path.append('C:\\Users\\nims\\Desktop\\demo-magician-python')

import DobotDllType as dType

class MagicianController:
    def __init__(self, COM="COM8"):
        #Load Dll and get the CDLL object
        self.api = dType.load()
        self.lastIndex = -1

        #Connect Dobot
        state = dType.ConnectDobot(self.api, COM, 115200)[0]
        CON_STR = {
            dType.DobotConnect.DobotConnect_NoError:  "DobotConnect_NoError",
            dType.DobotConnect.DobotConnect_NotFound: "DobotConnect_NotFound",
            dType.DobotConnect.DobotConnect_Occupied: "DobotConnect_Occupied"
        }
        #print("Connect status:", CON_STR[state])

        if state == dType.DobotConnect.DobotConnect_NoError:  
            dType.SetQueuedCmdClear(self.api)

            #Start to Execute Command Queue
            dType.SetQueuedCmdStartExec(self.api)

            #Async Motion Params Setting
            dType.SetHOMEParams(self.api, 200, 0, 0, 0, isQueued = 1)
            dType.SetPTPJointParams(self.api, 100, 100, 100, 100, 100, 100, 100, 100, isQueued = 1)
            self.lastIndex = dType.SetPTPCommonParams(self.api, 100, 100, isQueued = 1)[0]
        else:
            raise ConnectionError("Failed to connect Dobot")
    
    def wait(self):
        while self.lastIndex > dType.GetQueuedCmdCurrentIndex(self.api)[0]:
            dType.dSleep(100)
    
    def homing(self, wait=True):
        #Async Home
        self.lastIndex = dType.SetHOMECmd(self.api, 0, isQueued = 1)[0]

        if wait:
            self.wait()

    def move_arm(self, x=None, y=None, z=None, r=None, wait=True):
        current_position = dType.GetPose(self.api)[:4]
         
        if x is None:
            x = current_position[0]
        if y is None:
            y = current_position[1]
        if z is None:
            z = current_position[2]
        if r is None:
            r = current_position[3]

        d = math.sqrt(x**2 + y**2)
        if d < 115 or d > 320:
            raise ValueError("Dobot out of range")

        self.lastIndex = dType.SetPTPCmd(self.api, dType.PTPMode.PTPMOVLXYZMode, x, y, z, r, isQueued = 1)[0]
        if wait:
            self.wait()

    def move_arm_rel(self, x=0, y=0, z=0, r=0, wait=True):
        current_position = dType.GetPose(self.api)[:4]
        x = current_position[0] + x
        y = current_position[1] + y
        z = current_position[2] + z
        r = current_position[3] + r
        
        self.move_arm(x, y, z, r, wait=wait)

    def get_current_position(self):
        current_position = dType.GetPose(self.api)[:4]
        return current_position
    
    def get_current_joints(self):
        current_joints = dType.GetPose(self.api)[4:8]
        return current_joints

    def move_joints(self, j1=None, j2=None, j3=None, j4=None, wait=True):
        current_joints = dType.GetPose(self.api)[4:8]
         
        if j1 is None:
            j1 = current_joints[0]
        if j2 is None:
            j2 = current_joints[1]
        if j3 is None:
            j3 = current_joints[2]
        if j4 is None:
            j4 = current_joints[3]

        self.lastIndex = dType.SetPTPCmd(self.api, dType.PTPMode.PTPMOVJANGLEMode, j1, j2, j3, j4, isQueued = 1)[0]
        if wait:
            self.wait()

    def disconnect(self):
        #Disconnect Dobot
        dType.DisconnectDobot(self.api)

if __name__ == "__main__":
    dobot = MagicianController()
    dobot.move_arm(250, 0, 130)
    dobot.move_arm(250, 0, 100)
    dobot.move_arm(250, 0, 130)
    
    #dobot.homing()
    #dobot.move_joints(j1=90)
    #dobot.move_arm(0, -200, 60)