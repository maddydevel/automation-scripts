#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import os
import sys
import shutil
import time

# --- Configuration ---
VERIFY_INSTALLATION = True
ADD_USER_TO_DOCKER_GROUP = True
TARGET_OS_CODENAME = "noble" # For Ubuntu 24.04

# --- Helper Functions ---
def log_info(message):
    print(f"[INFO] {time.strftime('%Y-%m-%d %H:%M:%S')} - {message}")

def log_error(message):
    print(f"[ERROR] {time.strftime('%Y-%m-%d %H:%M:%S')} - {message}", file=sys.stderr)

def run_command(command, shell=False, check=True, capture_output=False, text=True, cwd=None, env=None, input_data=None):
    """Helper function to run a shell command."""
    log_info(f"Executing: {' '.join(command) if isinstance(command, list) else command}")
    try:
        process = subprocess.run(
            command,
            shell=shell,
            check=check,
            capture_output=capture_output,
            text=text,
            cwd=cwd,
            env=env,
            input=input_data
        )
        if capture_output:
            return process
        return True
    except subprocess.CalledProcessError as e:
        log_error(f"Command failed: {e}")
        if e.stdout:
            log_error(f"STDOUT: {e.stdout.strip()}")
        if e.stderr:
            log_error(f"STDERR: {e.stderr.strip()}")
        if check: # If check is True, CalledProcessError is raised, and we exit here.
             sys.exit(1)
        return False # If check is False, we return False on error
    except FileNotFoundError:
        log_error(f"Command not found: {command[0] if isinstance(command, list) else command.split()[0]}")
        if check:
            sys.exit(1)
        return False


def get_os_codename():
    """Gets the OS codename from /etc/os-release."""
    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                if line.startswith("VERSION_CODENAME="):
                    return line.strip().split("=")[1]
    except FileNotFoundError:
        log_error("/etc/os-release not found.")
        return None
    return None

def get_architecture():
    """Gets the system architecture."""
    result = run_command(["dpkg", "--print-architecture"], capture_output=True, text=True)
    if result and result.returncode == 0:
        return result.stdout.strip()
    log_error("Failed to determine system architecture.")
    sys.exit(1)

# --- Main Installation Logic ---
def main():
    log_info("Starting Docker installation script for Ubuntu...")

    # 1. Pre-flight Checks
    if os.geteuid() != 0:
        log_error("This script must be run as root. Please use sudo.")
        sys.exit(1)

    current_codename = get_os_codename()
    if current_codename != TARGET_OS_CODENAME:
        log_info(f"Warning: This script is intended for Ubuntu {TARGET_OS_CODENAME} (24.04).")
        log_info(f"Current OS codename is '{current_codename}'.")
        confirm = input("Do you want to continue anyway? (yes/no): ").lower()
        if confirm != "yes":
            log_info("Installation aborted by user.")
            sys.exit(0)
    else:
        log_info(f"Detected Ubuntu {current_codename}, proceeding.")


    # 2. Uninstall old versions
    log_info("Uninstalling potentially conflicting old Docker packages...")
    old_packages = ["docker", "docker-engine", "docker.io", "containerd", "runc"]
    # Run with check=False as packages might not be installed
    run_command(["apt-get", "remove", "-y"] + old_packages, check=False)
    run_command(["apt-get", "autoremove", "-y"], check=False) # Clean up dependencies
    run_command(["apt-get", "purge", "-y"] + old_packages, check=False) # Purge config files as well
    log_info("Finished uninstalling old packages (if any).")


    # 3. Set up the repository
    log_info("Updating apt package index and installing prerequisite packages...")
    run_command(["apt-get", "update"])
    prerequisites = ["ca-certificates", "curl", "gnupg"]
    run_command(["apt-get", "install", "-y"] + prerequisites)

    log_info("Adding Docker's official GPG key...")
    keyring_dir = "/etc/apt/keyrings"
    gpg_key_path = os.path.join(keyring_dir, "docker.gpg")

    os.makedirs(keyring_dir, mode=0o755, exist_ok=True)
    
    # Remove any existing key to ensure a clean state
    if os.path.exists(gpg_key_path):
        os.remove(gpg_key_path)
        log_info(f"Removed existing GPG key at {gpg_key_path}")

    # Using shell=True for the pipe, ensure the command is safe
    gpg_command = f"curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o {gpg_key_path}"
    if not run_command(gpg_command, shell=True): # shell=True for the pipe
        log_error("Failed to download and dearmor Docker GPG key.")
        sys.exit(1)
    
    if not os.path.exists(gpg_key_path):
        log_error(f"Docker GPG key not found at {gpg_key_path} after download attempt.")
        sys.exit(1)
    run_command(["chmod", "a+r", gpg_key_path])

    log_info("Setting up Docker's apt repository...")
    arch = get_architecture()
    repo_content = (
        f"deb [arch={arch} signed-by={gpg_key_path}] "
        f"https://download.docker.com/linux/ubuntu {current_codename} stable\n"
    )
    docker_list_file = "/etc/apt/sources.list.d/docker.list"
    
    # Write the repo file using tee via shell to handle permissions
    echo_tee_command = f"echo '{repo_content}' | tee {docker_list_file} > /dev/null"
    if not run_command(echo_tee_command, shell=True):
        log_error(f"Failed to write Docker repository list to {docker_list_file}")
        sys.exit(1)


    # 4. Install Docker Engine
    log_info("Updating apt package index after adding Docker repository...")
    run_command(["apt-get", "update"])

    log_info("Installing Docker Engine, CLI, containerd, and Docker Compose plugin...")
    docker_packages = [
        "docker-ce",
        "docker-ce-cli",
        "containerd.io",
        "docker-buildx-plugin",
        "docker-compose-plugin"
    ]
    run_command(["apt-get", "install", "-y"] + docker_packages)

    if not shutil.which("docker"):
        log_error("Docker command could not be found after installation. Something went wrong.")
        sys.exit(1)
    
    docker_version_result = run_command(["docker", "--version"], capture_output=True)
    if docker_version_result and docker_version_result.returncode == 0:
        log_info(f"Docker Engine installed successfully. Version: {docker_version_result.stdout.strip()}")
    else:
        log_warning("Could not retrieve Docker version, but installation command succeeded.")


    # 5. Verify installation (optional)
    if VERIFY_INSTALLATION:
        log_info("Verifying Docker installation by running the hello-world container...")
        # Run with check=False to evaluate success manually
        if run_command(["docker", "run", "hello-world"], check=False):
            log_info("Docker hello-world container ran successfully.")
        else:
            log_error("Failed to run Docker hello-world container. The installation might have issues.")
            # Not exiting here, as core installation might be okay.

    # 6. Post-installation steps: Manage Docker as a non-root user (optional)
    if ADD_USER_TO_DOCKER_GROUP:
        sudo_user = os.getenv("SUDO_USER")
        if sudo_user:
            log_info(f"Attempting to add user '{sudo_user}' to the 'docker' group...")
            # Check if group exists, create if not
            group_check_result = run_command(["getent", "group", "docker"], check=False, capture_output=True)
            if group_check_result.returncode != 0:
                log_info("Docker group does not exist. Creating it...")
                if not run_command(["groupadd", "docker"]):
                    log_error("Failed to create 'docker' group.")
                else:
                    log_info("'docker' group created.")
            
            # Add user to group
            if run_command(["usermod", "-aG", "docker", sudo_user]):
                log_info(f"User '{sudo_user}' added to the 'docker' group.")
                log_info(f"IMPORTANT: '{sudo_user}' needs to log out and log back in, or reboot, for this group change to take effect.")
            else:
                log_error(f"Failed to add user '{sudo_user}' to 'docker' group.")
        else:
            log_info("SUDO_USER variable is not set. Cannot automatically add user to 'docker' group.")
            log_info("If you want to run Docker as a non-root user, run the following commands manually:")
            log_info("  sudo groupadd docker  (if it doesn't exist)")
            log_info("  sudo usermod -aG docker YOUR_USERNAME")
            log_info("Then, log out and log back in, or reboot.")

    log_info("Docker installation and basic configuration completed.")
    log_info("Docker service should be started and enabled by default.")
    log_info("You can check status with: systemctl status docker")
    log_info("If not enabled/started, use: sudo systemctl enable --now docker")

if __name__ == "__main__":
    main()
