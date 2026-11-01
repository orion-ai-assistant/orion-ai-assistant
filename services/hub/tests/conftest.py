import os

# Worker requests carry the signed-in account's Router key; tests that build
# RuntimeSettings directly stand in for it here, never the Router admin password.
os.environ.setdefault('ROUTER_API_KEY', 'sk-orion-test-account')
