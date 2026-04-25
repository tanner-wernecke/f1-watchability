import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from f1_watchability.web.app import app
if __name__ == "__main__":
    app.run()
