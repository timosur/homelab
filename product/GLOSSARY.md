# Dark Factory

Terms used for work delegated to Dark Factory in the homelab.

## Language

**Task**:
A unit of delegated work that owns a task workspace and can have multiple sequential runs.

**Run**:
One execution of work for a task.

**Sandbox**:
The isolated environment in which a run executes.

**Project image**:
The credential-free container image that supplies a project's development tools inside a sandbox.

**Task workspace**:
The working area and cache shared by sequential runs of one task.

**Workspace owner**:
The sandbox currently allowed to use a task workspace. Under normal operation, a task workspace has at most one owner at a time.
