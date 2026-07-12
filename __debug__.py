print("=== DEBUG: START ===")
import os
print(f"Python version: {os.sys.version}")
print(f"Current dir: {os.getcwd()}")
print(f"Files: {os.listdir('.')}")

try:
    import bot
    print("=== bot.py imported successfully ===")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
