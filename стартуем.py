import subprocess
import sys


result = subprocess.run([sys.executable, "main.py"])

if __name__ == '__main__':
    print(result)