.PHONY: up down logs token status restart build

up:
	podman compose up -d

down:
	podman compose down

restart:
	podman compose restart

build:
	podman compose build --no-cache

logs:
	podman compose logs -f

token:
	zellij web --create-token

status:
	zellij web --status
	podman compose ps
