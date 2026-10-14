# Filesystem access

- The project folder is `/home/user/桌面/robot/detection`.
- Reading any file or directory outside the project folder requires the user's explicit approval before access.
- Writing, editing, creating, moving, or deleting any file or directory outside the project folder requires the user's explicit approval before the operation.
- Do not infer approval from filesystem permissions or tool availability. Ask the user first and identify the exact external path and intended operation.
