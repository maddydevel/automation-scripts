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

def run_command(command, shell=False, check=True, capture_output=False, text=True, cwd=None, env=None, input_string=None):
    """
    Helper function to run a shell command.

    Allows for providing input via `input_string` (can be str or bytes).
    If `input_string` is bytes, `text` should typically be False.
    When `input_string` is provided, `stdin` is set to `subprocess.PIPE`.
    """
    log_info(f"Executing: {' '.join(command) if isinstance(command, list) else command}")

    final_stdin_arg = None
    final_input_arg = None

    if input_string is not None:
        final_stdin_arg = subprocess.PIPE
        final_input_arg = input_string

    try:
        process = subprocess.run(
            command,
            shell=shell,
            check=check,
            capture_output=capture_output,
            text=text, # If True, decodes stdin/stdout/stderr as text. If False, they are bytes.
            cwd=cwd,
            env=env,
            input=final_input_arg,
            stdin=final_stdin_arg
        )
        # Logging of stdout/stderr if captured
        # Note: if text=False, process.stdout/stderr are bytes and strip() might fail if they are None.
        # If text=True, they are strings.
        if capture_output:
            if process.stdout:
                output_stdout = process.stdout.strip() if text else process.stdout.decode(errors='replace').strip()
                if output_stdout: # Only log if there's actual output after stripping
                    log_info(f"STDOUT:\n{output_stdout}")
            if process.stderr:
                output_stderr = process.stderr.strip() if text else process.stderr.decode(errors='replace').strip()
                if output_stderr: # Only log if there's actual output after stripping
                    log_info(f"STDERR:\n{output_stderr}")

        return process if capture_output else (process.returncode == 0)
    except subprocess.CalledProcessError as e:
        log_error(f"Command failed: {e}")
        if e.stdout: # Already decoded due to text=True
            log_error(f"STDOUT: {e.stdout.strip()}")
        if e.stderr: # Already decoded
            log_error(f"STDERR: {e.stderr.strip()}")
        # No need to sys.exit(1) here if check=True, as it's already raised.
        # The original 'if check: sys.exit(1)' was redundant due to check=True behavior.
        # If check is False, we fall through and return based on success.
        if not check: # Only return False if check is False, otherwise error is raised.
            return False
        # If check is True, this part is not reached due to exception.
        # For safety, ensure a clear path for check=True failure, though exception is primary.
        raise
    except FileNotFoundError:
        log_error(f"Command not found: {command[0] if isinstance(command, list) else command.split()[0]}")
        if check:
            raise # Re-raise the exception if check is True
        return False
    except Exception as e: # Catch any other unexpected errors
        log_error(f"An unexpected error occurred: {e}")
        if check:
            raise
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
        log_info(f"Removed existing Docker GPG key at {gpg_key_path}.")
    else:
        log_info(f"No existing Docker GPG key found at {gpg_key_path}. Proceeding to download.")

    log_info("Downloading Docker's GPG key...")
    curl_result = run_command(
        ["curl", "-fsSL", "https://download.docker.com/linux/ubuntu/gpg"],
        capture_output=True,
        text=False,  # GPG key is binary
        check=True # Exit if curl fails
    )
    if not (curl_result and curl_result.stdout):
        log_error("Failed to download Docker's GPG key (curl command output was empty).")
        sys.exit(1)

    log_info(f"Dearmoring Docker's GPG key to {gpg_key_path}...")
    # Pass the binary stdout from curl to gpg's stdin.
    # `run_command` for gpg will receive bytes via input_stream and pass to stdin=input_stream.
    # gpg --dearmor expects binary input.
    # The `text=True` default in run_command is for decoding output, not for stdin handling here.
    # We explicitly pass `curl_result.stdout` (bytes) to `input_stream`.
    # `subprocess.run` will handle this correctly. If gpg needs text, it would fail.
    # It's better to let `gpg` handle the input as a stream of bytes.
    # Therefore, when calling run_command with bytes in input_string, text must be False.
    if not run_command(
        ["gpg", "--dearmor", "-o", gpg_key_path],
        input_string=curl_result.stdout, # stdout from curl is bytes (text=False in that call)
        text=False, # Crucial: input_string is bytes, so subprocess should handle it as bytes
        check=True, # Exit if gpg fails
        capture_output=True # To log any potential stderr from gpg (will be decoded for logging)
    ):
        # log_error is already called by run_command on failure with check=True,
        # but we might want a more specific message before sys.exit if run_command didn't exit.
        # However, with check=True, run_command *will* raise an exception, so this path isn't taken.
        # The log_error in run_command itself will be the primary error message.
        log_error("Failed to dearmor Docker's GPG key using the gpg command.") # This is more of a fallback.
        sys.exit(1)
    
    if not os.path.exists(gpg_key_path):
        log_error(f"Docker GPG key file not found at {gpg_key_path} after dearmoring attempt.")
        sys.exit(1)

    log_info(f"Setting read permissions for Docker GPG key at {gpg_key_path}...")
    run_command(["chmod", "a+r", gpg_key_path])

    log_info("Configuring Docker's APT repository...")
    arch = get_architecture()
    # current_codename is used here, which is correct as it's been validated or confirmed by the user.
    repo_content = (
        f"deb [arch={arch} signed-by={gpg_key_path}] "
        f"https://download.docker.com/linux/ubuntu {current_codename} stable\n"
    )
    docker_list_file = "/etc/apt/sources.list.d/docker.list"

    # Idempotency check for Docker repository configuration
    write_repo_file = True
    if os.path.exists(docker_list_file):
        try:
            with open(docker_list_file, "r") as f:
                existing_content = f.read()
            # Normalize both new and existing content for comparison
            # Stripping whitespace handles potential differences in trailing newlines etc.
            if existing_content.strip() == repo_content.strip():
                log_info(f"Docker APT repository at {docker_list_file} is already configured correctly.")
                write_repo_file = False
            else:
                log_info(f"Docker APT repository at {docker_list_file} exists but content differs. Overwriting...")
        except IOError as e:
            log_warning(f"Could not read existing Docker APT repository file at {docker_list_file}: {e}. Proceeding to write/overwrite.")
    
    if write_repo_file:
        log_info(f"Writing Docker APT repository configuration to {docker_list_file}...")
        try:
            with open(docker_list_file, "w") as f:
                f.write(repo_content)
            log_info(f"Successfully wrote Docker APT repository configuration to {docker_list_file}.")
        except IOError as e:
            log_error(f"Failed to write Docker APT repository configuration to {docker_list_file}: {e}")
            sys.exit(1)
    else:
        # This case is when the file exists and content matches.
        # The "apt-get update" is still important even if we didn't write the file this time,
        # as other sources might have changed or it's the first run.
        pass


    # 4. Install Docker Engine
    log_info("Updating apt package index after adding Docker repository (if changed or first time)...")
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
        log_info("Verifying Docker installation by running the 'hello-world' container...")
        # Run with check=False to evaluate success manually
        verify_result = run_command(["docker", "run", "hello-world"], check=False, capture_output=True)
        if verify_result and verify_result.returncode == 0:
            # run_command already logs stdout for hello-world if capture_output=True
            log_info("Docker 'hello-world' container ran successfully.")
        else:
            log_error("Failed to run Docker 'hello-world' container. The installation might have issues.")
            # verify_result.stderr is logged by run_command if capture_output=True
            # Adding an extra log here might be redundant if run_command's logging is sufficient.
            # if verify_result and verify_result.stderr:
            #      log_error(f"Hello-world STDERR: {verify_result.stderr.strip()}")
    else:
        log_info("Skipping Docker installation verification (hello-world container).")

    # 6. Post-installation steps: Manage Docker as a non-root user (optional)
    if ADD_USER_TO_DOCKER_GROUP:
        log_info("Attempting to configure Docker for non-root user access...")
        sudo_user = os.getenv("SUDO_USER")
        if sudo_user:
            log_info(f"Checking if user '{sudo_user}' needs to be added to the 'docker' group.")
            # Check if group exists
            group_exists_result = run_command(["getent", "group", "docker"], check=False, capture_output=True)
            if group_exists_result.returncode != 0:
                log_info("Docker group does not exist. Creating 'docker' group...")
                if run_command(["groupadd", "docker"], check=False):
                    log_info("'docker' group created successfully.")
                else:
                    log_error("Failed to create 'docker' group. User management may fail.")
            else:
                log_info("'docker' group already exists.")
            
            # Add user to group if not already a member
            # Checking membership is a bit more complex with `getent group docker` or `groups $USER`
            # For simplicity, usermod -aG is idempotent in effect (won't add if already there, won't error).
            log_info(f"Adding user '{sudo_user}' to the 'docker' group (if not already a member)...")
            if run_command(["usermod", "-aG", "docker", sudo_user], check=False):
                log_info(f"User '{sudo_user}' successfully added to the 'docker' group.")
                log_info(f"IMPORTANT: '{sudo_user}' needs to log out and log back in, or reboot, for this group change to take effect.")
            else:
                log_error(f"Failed to add user '{sudo_user}' to the 'docker' group.")
        else:
            log_warning("SUDO_USER environment variable not found. Cannot automatically add user to 'docker' group.")
            log_info("To run Docker as a non-root user, please perform the following steps manually:")
            log_info("  1. Create the 'docker' group (if it doesn't exist): sudo groupadd docker")
            log_info("  2. Add your user to the 'docker' group: sudo usermod -aG docker YOUR_USERNAME")
            log_info("  3. Log out and log back in, or reboot, for the changes to take effect.")
    else:
        log_info("Skipping non-root user configuration for Docker.")

    log_info("Docker installation script finished.")
    log_info("Docker service should be started and enabled by default on systemd systems.")
    log_info("You can check status with: systemctl status docker")
    log_info("If not enabled/started, use: sudo systemctl enable --now docker")

def log_warning(message):
    print(f"[WARNING] {time.strftime('%Y-%m-%d %H:%M:%S')} - {message}", file=sys.stderr)

if __name__ == "__main__":
    main()
