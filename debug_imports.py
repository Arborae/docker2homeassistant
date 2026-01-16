import sys
import os
from pathlib import Path

print("Python executable:", sys.executable)
print("CWD:", os.getcwd())
print("Sys Path:", sys.path)

try:
    import flask
    print("Flask ok")
except ImportError as e:
    print("Flask missing:", e)

try:
    import docker
    print("Docker ok")
except ImportError as e:
    print("Docker missing:", e)

# Add d2ha to path like tests do
d2ha_path = os.path.join(os.getcwd(), "d2ha")
sys.path.append(d2ha_path)
print("Added d2ha to path:", d2ha_path)

try:
    import services.docker
    print("d2ha.services.docker ok")
except ImportError as e:
    print("d2ha.services.docker fail:", e)

try:
    import app
    print("d2ha.app ok")
except ImportError as e:
    print("d2ha.app fail:", e)
