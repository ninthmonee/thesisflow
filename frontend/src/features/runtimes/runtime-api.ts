import {
  createRuntimeConnection,
  deleteRuntimeConnection as removeConnection,
  listRuntimeConnections as getConnections,
  listRuntimeTypes as getTypes,
  testRuntimeConnection as probeConnection,
  updateRuntimeConnection,
} from "../../shared/api/generated/client";
import type { RuntimeConnection, RuntimeEditorFields } from "./runtime-state";
import { runtimeConnectionBody, runtimeUpdateBody } from "./runtime-state";

export const listRuntimeTypes = (signal?: AbortSignal) => getTypes({ signal });
export const listRuntimeConnections = (signal?: AbortSignal) => getConnections({ signal });
export const saveRuntimeConnection = (
  fields: RuntimeEditorFields,
  current?: RuntimeConnection | null,
) =>
  current
    ? updateRuntimeConnection(current.id, runtimeUpdateBody(fields, current.revision))
    : createRuntimeConnection(runtimeConnectionBody(fields));
export const testRuntimeConnection = (current: RuntimeConnection) =>
  probeConnection(current.id, { revision: current.revision });
export const deleteRuntimeConnection = (current: RuntimeConnection) =>
  removeConnection(current.id, { revision: current.revision });
