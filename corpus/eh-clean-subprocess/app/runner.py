import subprocess

def run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True)
