include .env
export

VITE_BACKEND_HOST ?= 127.0.0.1
VITE_BACKEND_PORT ?= 8000
VITE_FRONTEND_HOST ?= 0.0.0.0
VITE_FRONTEND_PORT ?= 5173

build-all: build-h5

dev:
	@echo "Local mode: http://127.0.0.1:$(VITE_BACKEND_PORT) (backend) + http://127.0.0.1:$(VITE_FRONTEND_PORT) (frontend)"
	@trap 'kill 0' INT TERM EXIT; \
	( cd agents-stage-live2d-vrm3d-server && .venv/bin/python main.py --host $(VITE_BACKEND_HOST) --port $(VITE_BACKEND_PORT) ) & \
	( cd agents-stage-live2d-vrm3d-fe && npm run dev ) & \
	wait

dev-remote:
	@echo "Remote mode: building frontend..."
	cd agents-stage-live2d-vrm3d-fe && npm run build
	@echo "Starting remote server with auth..."
	@trap 'kill 0' INT TERM EXIT; \
	( cd agents-stage-live2d-vrm3d-server && \
	  .venv/bin/python main.py --host $(VITE_BACKEND_HOST) --port $(VITE_BACKEND_PORT) \
	  --mode remote \
	  --config ../config.json \
	  --static-path ../agents-stage-live2d-vrm3d-fe/dist ) & \
	( cloudflared tunnel run agents-stage ) & \
	wait

build-h5:
	cd agents-stage-live2d-vrm3d-fe && npm run build
