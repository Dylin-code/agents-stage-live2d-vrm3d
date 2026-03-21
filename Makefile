build-all: build-h5

dev:
	@echo "Local mode: http://127.0.0.1:8000 (backend) + http://127.0.0.1:5173 (frontend)"
	@trap 'kill 0' INT TERM EXIT; \
	( cd agents-stage-live2d-vrm3d-server && .venv/bin/python main.py --host 127.0.0.1 --port 8000 ) & \
	( cd agents-stage-live2d-vrm3d-fe && npm run dev ) & \
	wait

dev-remote:
	@echo "Remote mode: building frontend..."
	cd agents-stage-live2d-vrm3d-fe && npm run build
	@echo "Starting remote server with auth..."
	@trap 'kill 0' INT TERM EXIT; \
	( cd agents-stage-live2d-vrm3d-server && \
	  .venv/bin/python main.py --host 127.0.0.1 --port 8000 \
	  --mode remote \
	  --config ../config.json \
	  --static-path ../agents-stage-live2d-vrm3d-fe/dist ) & \
	( cloudflared tunnel run agents-stage ) & \
	wait

build-h5:
	cd agents-stage-live2d-vrm3d-fe && npm run build
