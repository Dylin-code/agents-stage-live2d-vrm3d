build-all: build-h5

dev:
	@echo "Starting backend on http://127.0.0.1:8000 and frontend on http://127.0.0.1:5173"
	@trap 'kill 0' INT TERM EXIT; \
	( cd agents-stage-live2d-vrm3d-server && .venv/bin/python main.py --host 127.0.0.1 --port 8000 ) & \
	( cd agents-stage-live2d-vrm3d-fe && npm run dev ) & \
	wait

build-h5:
	cd agents-stage-live2d-vrm3d-fe && npm run build
