# Dev Apps Directory

Place your local app sources here for development with hot-reload.
Each app goes in its own subdirectory named by app-id.

## Directory Structure

```
dev-apps/
  estimator/           -> symlink to ~/Code/estimator
  project-analysis/    -> symlink to ~/Code/project-analysis
  my-custom-app/       -> actual source or symlink
```

## Usage

1. **Symlink your apps** (one per subdirectory):
   ```bash
   ln -s ~/Code/estimator ./estimator
   ln -s ~/Code/project-analysis ./project-analysis
   ```

2. **Register each app in AI Portal** with "Dev Mode" enabled

3. **For hot-reload during active development:**
   ```bash
   docker exec -it local-user-apps bash
   cd /srv/dev-apps/estimator && npm run dev
   ```

4. **For production-like testing:**
   Deploy via AI Portal (builds app and creates systemd service)

## Notes

- App subdirectory name must match the app-id in AI Portal
- Multiple apps can run simultaneously on different ports
- Each app gets its own systemd service when deployed
- Changes to source files are immediately visible in the container
- No container rebuild needed - just add/remove symlinks

## How It Works

The `dev-apps/` directory is mounted at `/srv/dev-apps/` in the `user-apps` container.
When you trigger a deployment via AI Portal:

1. Deploy-api checks if `/srv/dev-apps/{app-id}/` exists
2. If found, it uses the dev path instead of git cloning
3. It runs `npm install && npm run build`
4. Creates/updates the systemd service
5. Starts the app

For active development, skip the deploy step and just run `npm run dev` directly
in the container for full hot-reload support.
