ifeq ($(OS),Windows_NT)
SHELL := C:/Program Files/Git/bin/bash.exe
.SHELLFLAGS := -c
endif

include .env
export

VITE_BACKEND_HOST ?= 127.0.0.1
VITE_BACKEND_PORT ?= 8000
VITE_FRONTEND_HOST ?= 0.0.0.0
VITE_FRONTEND_PORT ?= 5173

# Detect Python venv path (Windows: Scripts/python, Unix: bin/python)
VENV_PYTHON := $(shell if [ -f agents-stage-live2d-vrm3d-server/.venv/Scripts/python.exe ]; then echo .venv/Scripts/python; else echo .venv/bin/python; fi)

build-all: build-h5

dev:
	@echo "Local mode: http://127.0.0.1:$(VITE_BACKEND_PORT) (backend) + http://127.0.0.1:$(VITE_FRONTEND_PORT) (frontend)"
	@trap 'kill 0' INT TERM EXIT; \
	( cd agents-stage-live2d-vrm3d-server && PYTHONUNBUFFERED=1 $(VENV_PYTHON) main.py --host $(VITE_BACKEND_HOST) --port $(VITE_BACKEND_PORT) 2>&1 ) & \
	( cd agents-stage-live2d-vrm3d-fe && npm run dev ) & \
	( if [ "$$(uname)" = "Darwin" ]; then \
		while ! curl -sS "http://127.0.0.1:$(VITE_FRONTEND_PORT)/desktop-widget" >/dev/null 2>&1; do sleep 1; done; \
		cd agents-stage-live2d-vrm3d-fe && DESKTOP_WIDGET_URL="http://127.0.0.1:$(VITE_FRONTEND_PORT)/desktop-widget" npm run electron:dev; \
	  fi ) & \
	wait

dev-remote:
	@echo "Remote mode: building frontend..."
	cd agents-stage-live2d-vrm3d-fe && npm run build
	@echo "Starting remote server with auth..."
	@trap 'kill 0' INT TERM EXIT; \
	( cd agents-stage-live2d-vrm3d-server && \
	  $(VENV_PYTHON) main.py --host $(VITE_BACKEND_HOST) --port $(VITE_BACKEND_PORT) \
	  --mode remote \
	  --config ../config.json \
	  --static-path ../agents-stage-live2d-vrm3d-fe/dist ) & \
	( cloudflared tunnel run agents-stage ) & \
	wait

build-h5:
	cd agents-stage-live2d-vrm3d-fe && npm run build
