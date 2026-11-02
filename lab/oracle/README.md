# Oracle container lab

The first lab image runs the complete regression suite as an unprivileged user in an isolated container. It has no host mounts, provider credentials, SSH agent, Docker socket or published ports. It uses the source copied at image build time. Containers test Linux user-space behavior; they do not establish full VM, systemd, kernel sandbox, Windows or macOS compatibility.

Build from the repository root with `docker build -f lab/oracle/Dockerfile -t loopback-lab:recovery .`. Run with `docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges loopback-lab:recovery`. Preserve terminal exit and test results. Installer and multi-host network scenarios will have separate runs and manifests.
