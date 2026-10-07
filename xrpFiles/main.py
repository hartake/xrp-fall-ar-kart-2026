import os
import sys
import time
FILE_PATH = '/lib/ble/isrunning'
doNothing = False
x = os.dupterm(None, 0)
if(x == None):
   import ble.blerepl
else:
   os.dupterm(x,0)
try:
   with open(FILE_PATH, 'r+b') as file:
      byte = file.read(1)
      if byte == b'\x01':
         file.seek(0)
         file.write(b'\x00')
         doNothing = True
   if(not doNothing):
       with open('/xrp_raw_sensors.py', mode='r') as exfile:
           code = exfile.read()
       execCode = compile(code, 'xrp_raw_sensors.py', 'exec')
       exec(execCode)
except Exception as e:
   import sys
   sys.print_exception(e)
finally:
   import gc
   gc.collect()
   if 'XRPLib.resetbot' in sys.modules:
      del sys.modules['XRPLib.resetbot']
   import XRPLib.resetbot
