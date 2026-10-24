import { DurableObject } from "cloudflare:workers";
import type { StorageEnv } from "./index";

export const NOTIFICATION_PROTOCOL = "textfold/changes-ws-v1";

export function notificationsEnabled(env: StorageEnv): boolean {
  return Boolean(env.NOTIFICATIONS) && env.NOTIFICATIONS_DISABLED !== "true";
}

export class LibraryNotifications extends DurableObject<StorageEnv> {
  async fetch(request: Request): Promise<Response> {
    const library = request.headers.get("X-TextFold-Library");
    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") return new Response(null, { status: 426 });
    if (!library || !/^[\w-]{1,200}$/.test(library)) return new Response(null, { status: 400 });
    const [client, server] = Object.values(new WebSocketPair());
    this.ctx.acceptWebSocket(server, [library]);
    server.serializeAttachment({ library });
    server.send(JSON.stringify({ type: "ready", protocol: NOTIFICATION_PROTOCOL, library_id: library }));
    return new Response(null, { status: 101, webSocket: client });
  }

  async changed(library: string, cursor: number): Promise<void> {
    if (!Number.isSafeInteger(cursor) || cursor < 0) throw new Error("Invalid notification cursor");
    const message = JSON.stringify({ type: "changed", library_id: library, cursor });
    // Enumerate platform-managed connections after every wake-up. No in-memory
    // subscription map, timers or second authoritative cursor survive here.
    for (const socket of this.ctx.getWebSockets(library)) {
      if (socket.deserializeAttachment()?.library !== library) continue;
      try { socket.send(message); }
      catch {
        try { socket.close(1011, "Notification connection closed"); }
        catch { /* A dead peer must not interrupt delivery to other devices. */ }
      }
    }
  }

  async webSocketMessage(socket: WebSocket, _message: string | ArrayBuffer): Promise<void> {
    socket.close(1008, "Notifications are receive-only");
  }

  async webSocketClose(socket: WebSocket, code: number): Promise<void> {
    socket.close([1005, 1006, 1015].includes(code) ? 1000 : code);
  }

  async webSocketError(socket: WebSocket): Promise<void> {
    socket.close(1011, "Notification connection closed");
  }
}

export async function publishChanges(env: StorageEnv): Promise<void> {
  if (!notificationsEnabled(env)) return;
  const db = env.DB.withSession("first-primary");
  const library = await db.prepare("SELECT library_id FROM storage_identity WHERE singleton=1").first<{ library_id: string }>();
  const head = await db.prepare("SELECT COALESCE(MAX(sequence),0) AS cursor FROM change_log").first<{ cursor: number }>();
  await env.NOTIFICATIONS.getByName("library-notifications:" + library!.library_id).changed(library!.library_id, head!.cursor);
}
