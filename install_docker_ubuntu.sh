#!/bin/bash

# Script for unattended installation of Docker on Ubuntu 24.04
# Based on: https://docs.docker.com/engine/install/ubuntu/

# Exit immediately if a command exits with a non-zero status.
set -e
# Treat unset variables as an error when substituting.
set -u
# Return value of a pipeline is the status of the last command to exit with a non-zero status.
set -o pipefail

# --- Configuration ---
# Set to true to automatically run hello-world container for verification
VERIFY_INSTALLATION=true
# Set to true to attempt to add the $SUDO_USER to the docker group
ADD_USER_TO_DOCKER_GROUP=true

# --- Helper Functions ---
log_info() {
    echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

# --- Pre-flight Checks ---
if [ "$(id -u)" -ne 0 ]; then
  log_error "This script must be run as root. Please use sudo."
  exit 1
fi

if ! grep -q "VERSION_CODENAME=noble" /etc/os-release; then
    CURRENT_CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
    log_info "Warning: This script is intended for Ubuntu 24.04 (noble)."
    log_info "Current OS codename is '$CURRENT_CODENAME'."
    read -r -p "Do you want to continue anyway? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        log_info "Installation aborted by user."
        exit 0
    fi
fi

# --- Main Installation ---
log_info "Starting Docker installation for Ubuntu..."

# 1. Uninstall old versions
log_info "Uninstalling potentially conflicting old Docker packages..."
apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || log_info "No old Docker packages to remove or some were not installed."

# 2. Set up the repository
log_info "Updating apt package index and installing prerequisite packages..."
apt-get update
apt-get install -y \
    ca-certificates \
    curl \
    gnupg

log_info "Adding Docker's official GPG key..."
install -m 0755 -d /etc/apt/keyrings
# Remove any existing key to ensure a clean state
rm -f /etc/apt/keyrings/docker.gpg
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
    log_error "Failed to download Docker GPG key."
    exit 1
fi
chmod a+r /etc/apt/keyrings/docker.gpg

log_info "Setting up Docker's apt repository..."
# Get OS codename (e.g., noble for 24.04)
OS_CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
# Get system architecture (e.g., amd64, arm64)
ARCH=$(dpkg --print-architecture)

echo \
  "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  ${OS_CODENAME} stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

# 3. Install Docker Engine
log_info "Updating apt package index after adding Docker repository..."
apt-get update

log_info "Installing Docker Engine, CLI, containerd, and Docker Compose plugin..."
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

if ! command -v docker &> /dev/null; then
    log_error "Docker command could not be found after installation. Something went wrong."
    exit 1
fi
log_info "Docker Engine installed successfully. Version: $(docker --version)"

# 4. Verify installation (optional)
if [ "$VERIFY_INSTALLATION" = true ]; then
    log_info "Verifying Docker installation by running the hello-world container..."
    if docker run hello-world; then
        log_info "Docker hello-world container ran successfully."
    else
        log_error "Failed to run Docker hello-world container. The installation might have issues."
        # Not exiting here, as core installation might be okay, but user should check.
    fi
fi

# 5. Post-installation steps: Manage Docker as a non-root user (optional)
if [ "$ADD_USER_TO_DOCKER_GROUP" = true ]; then
    if [ -n "${SUDO_USER-}" ]; then # Check if SUDO_USER is set and not empty
        log_info "Adding user '${SUDO_USER}' to the 'docker' group..."
        if ! getent group docker > /dev/null; then
            log_info "Creating 'docker' group..."
            groupadd docker
        fi
        usermod -aG docker "${SUDO_USER}"
        log_info "User '${SUDO_USER}' added to the 'docker' group."
        log_info "IMPORTANT: '${SUDO_USER}' needs to log out and log back in, or reboot, for this group change to take effect."
    else
        log_info "SUDO_USER variable is not set. Cannot automatically add user to 'docker' group."
        log_info "If you want to run Docker as a non-root user, run the following commands manually:"
        log_info "  sudo groupadd docker  (if it doesn't exist)"
        log_info "  sudo usermod -aG docker YOUR_USERNAME"
        log_info "Then, log out and log back in, or reboot."
    fi
fi

log_info "Docker installation and basic configuration completed."
log_info "Docker service should be started and enabled by default."
log_info "You can check status with: systemctl status docker"
log_info "If not enabled/started, use: sudo systemctl enable --now docker"

exit 0