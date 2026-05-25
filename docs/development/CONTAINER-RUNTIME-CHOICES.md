# Local Container Runtime Choices

The repo does not require Docker Compose as the local development contract.
`make dev` uses Compose only when Docker is already reachable. Otherwise it uses
native Python mocks and expects a local AWS-compatible emulator at
`AWS_ENDPOINT_URL`.

Run this first:

```bash
make bootstrap-runtime
```

That command audits Docker Engine, Podman, Rancher Desktop/nerdctl, and prints
the next command path for the current workstation.

## Recommendation

For minimal WSL2 platform operators, prefer this order:

1. Podman inside the Ubuntu WSL distro. Run only the local AWS emulator as a
   container; the repo starts its Python mocks natively with `make dev-native`.
2. Docker Engine inside the Ubuntu WSL distro when you want the most compatible
   Linux-native Compose path.
3. Rancher Desktop on Windows with WSL integration when a GUI managed runtime is
   acceptable. Use dockerd/Moby mode when you want Docker CLI and Compose
   compatibility.

Compose is convenience only. The functional contract is:

- local AWS emulator on `AWS_ENDPOINT_URL`, default `http://localhost:4566`
- health endpoint on `LOCAL_AWS_HEALTH_URL`
- mock Runtime on `:8765`
- mock JWKS on `:8766`
- local bridge API on `:8080`

## Runtime Notes

Docker Engine is the most compatible option for `docker compose`. Docker's
Ubuntu documentation lists Ubuntu 24.04 LTS as supported and installs the
Compose plugin through `docker-compose-plugin`.

Podman is available from Ubuntu repositories on modern Ubuntu releases and can
run the emulator image directly:

```bash
sudo apt-get update
sudo apt-get install -y podman

podman run --rm -p 4566:4566 \
  -e SERVICES=dynamodb,s3,sqs,ssm,secretsmanager,events \
  docker.io/floci/floci:latest

AWS_ENDPOINT_URL=http://localhost:4566 make dev-native
```

This is the minimal WSL path because it avoids Docker Desktop, Rancher Desktop,
Docker Engine, and Compose. It still gives the repo a local AWS-compatible
endpoint while leaving the Runtime/JWKS/API mocks as native Python processes.

Rancher Desktop supports both containerd/nerdctl and dockerd/Moby. Choose
dockerd/Moby if you want the normal Docker CLI and Compose path; choose
containerd/nerdctl if you only need to run the local AWS emulator image and use
`make dev-native`.

References checked 2026-05-25:

- Docker Engine on Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- Docker Compose plugin: https://docs.docker.com/compose/install/
- Podman installation: https://podman.io/docs/installation
- Rancher Desktop container runtime preferences: https://docs.rancherdesktop.io/ui/preferences/container-engine/general
