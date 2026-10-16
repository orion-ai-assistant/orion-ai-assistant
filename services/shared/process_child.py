"""Standard-library-only graceful control adapter for Python applications."""
import sys
import time
from pathlib import Path

def child_main():
    """Python control adapter: interrupt the app in-process for graceful shutdown."""
    import _thread
    import runpy
    import threading
    control = Path(sys.argv[1])
    arguments = sys.argv[2:]
    def watch():
        while not control.exists():
            time.sleep(.1)
        _thread.interrupt_main()
    threading.Thread(target=watch, daemon=True).start()
    try:
        if arguments[0] == '-m':
            sys.argv = arguments[1:]
            runpy.run_module(sys.argv[0], run_name='__main__', alter_sys=True)
        else:
            sys.argv = arguments
            sys.path.insert(0, str(Path(arguments[0]).resolve().parent))
            runpy.run_path(arguments[0], run_name='__main__')
    except KeyboardInterrupt:
        pass



if __name__ == '__main__':
    child_main()
