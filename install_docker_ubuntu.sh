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
log_info "Starting Docker installation script for Ubuntu..."

# 1. Uninstall old versions
log_info "Uninstalling potentially conflicting old Docker packages (if any)..."
OLD_PACKAGES=("docker" "docker-engine" "docker.io" "containerd" "runc")
# apt-get remove will print a notice if packages are not installed, but won't error.
apt-get remove -y "${OLD_PACKAGES[@]}"
# Purge configuration files of old packages
log_info "Purging configuration files of old Docker packages (if any)..."
apt-get purge -y "${OLD_PACKAGES[@]}"
# Autoremove unused dependencies
log_info "Autoremoving unused dependencies..."
apt-get autoremove -y
log_info "Finished uninstalling and cleaning up old Docker packages."

# 2. Set up the repository
log_info "Updating APT package index and installing prerequisite packages..."
apt-get update
apt-get install -y \
    ca-certificates \
    curl \
    gnupg

log_info "Ensuring Docker GPG keyring directory exists at /etc/apt/keyrings..."
install -m 0755 -d /etc/apt/keyrings

log_info "Adding Docker's official GPG key..."
# Remove any existing key to ensure a clean state and idempotency
if [ -f /etc/apt/keyrings/docker.gpg ]; then
    rm -f /etc/apt/keyrings/docker.gpg
    log_info "Removed existing Docker GPG key."
fi
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# Verify GPG key was added
if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
    log_error "Failed to download or dearmor Docker's GPG key."
    exit 1
fi
log_info "Docker GPG key added successfully."
chmod a+r /etc/apt/keyrings/docker.gpg
log_info "Set read permissions for Docker GPG key."

log_info "Configuring Docker's APT repository..."
# Use the possibly updated OS_CODENAME from the pre-flight check section if the user confirmed.
# However, the script re-reads it here. For consistency, it might be better to pass it or rely on the initial check's value.
# For now, keeping the script's current logic of re-reading.
OS_CODENAME_FOR_REPO=$(. /etc/os-release && echo "$VERSION_CODENAME")
# Get system architecture (e.g., amd64, arm64)
ARCH=$(dpkg --print-architecture)

REPO_STRING="deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${OS_CODENAME_FOR_REPO} stable"
# Idempotency: Check if repo is already configured correctly
DOCKER_LIST_FILE="/etc/apt/sources.list.d/docker.list"
if [ -f "$DOCKER_LIST_FILE" ] && grep -qF "$REPO_STRING" "$DOCKER_LIST_FILE"; then
    log_info "Docker APT repository already configured correctly in $DOCKER_LIST_FILE."
else
    log_info "Writing Docker APT repository configuration to $DOCKER_LIST_FILE..."
    echo "$REPO_STRING" | tee "$DOCKER_LIST_FILE" > /dev/null
    log_info "Docker APT repository configured."
fi


# 3. Install Docker Engine
log_info "Updating APT package index after configuring Docker repository..."
apt-get update

log_info "Installing Docker Engine, CLI, containerd, and Docker Compose plugin..."
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

if ! command -v docker &> /dev/null; then
    log_error "Docker command could not be found after installation. Something went wrong."
    exit 1
fi
# Capture only the version string for cleaner logging
DOCKER_VERSION=$(docker --version)
log_info "Docker Engine installed successfully. Version: $DOCKER_VERSION"

# 4. Verify installation (optional)
if [ "$VERIFY_INSTALLATION" = true ]; then
    log_info "Verifying Docker installation by running the 'hello-world' container..."
    if docker run hello-world; then
        log_info "Docker 'hello-world' container ran successfully."
    else
        log_error "Failed to run Docker 'hello-world' container. The installation might have issues."
        # Not exiting here, as core installation might be okay, but user should check.
    fi
else
    log_info "Skipping Docker installation verification ('hello-world' container)."
fi

# 5. Post-installation steps: Manage Docker as a non-root user (optional)
if [ "$ADD_USER_TO_DOCKER_GROUP" = true ]; then
    log_info "Configuring Docker for non-root user access..."
    if [ -n "${SUDO_USER-}" ]; then # Check if SUDO_USER is set and not empty
        log_info "Attempting to add user '${SUDO_USER}' to the 'docker' group..."
        if ! getent group docker > /dev/null; then
            log_info "'docker' group does not exist. Creating it..."
            if groupadd docker; then
                log_info "'docker' group created successfully."
            else
                log_error "Failed to create 'docker' group. Please check system logs."
                # Continue, as Docker itself is installed.
            fi
        else
            log_info "'docker' group already exists."
        fi

        log_info "Adding user '${SUDO_USER}' to 'docker' group (if not already a member)..."
        if usermod -aG docker "${SUDO_USER}"; then
            log_info "User '${SUDO_USER}' successfully added to the 'docker' group."
            log_info "IMPORTANT: User '${SUDO_USER}' needs to log out and log back in, or reboot, for this group change to take effect."
        else
            log_error "Failed to add user '${SUDO_USER}' to the 'docker' group. Please check system logs."
        fi
    else
        log_warning "SUDO_USER environment variable is not set. Cannot automatically add user to 'docker' group."
        log_info "To run Docker as a non-root user, please perform the following steps manually:"
        log_info "  1. Create the 'docker' group (if it doesn't exist): sudo groupadd docker"
        log_info "  2. Add your user to the 'docker' group: sudo usermod -aG docker YOUR_USERNAME"
        log_info "  3. Log out and log back in, or reboot, for the changes to take effect."
    fi
else
    log_info "Skipping non-root user configuration for Docker."
fi

log_info "Docker installation script finished."
log_info "Docker service should be started and enabled by default on systemd systems."
log_info "You can check status with: systemctl status docker"
log_info "If not enabled/started, use: sudo systemctl enable --now docker"

exit 0