/**
 * Commands: what an answer can hand back to a client that acts on it.
 *
 * A question or a human reply may say which app it was written from
 * (`client`). When that app can run commands on the asker's machine — today
 * only Zed, the level editor in Abyss, whose Zoey panel lists them and runs
 * the one the human picks — the advisor may attach a list of them to its
 * answer or reply, so the change being asked for is one click away instead of
 * a set of steps to follow by hand. Nothing runs by itself: each command is a
 * line of the client's own command language, shown to the human first.
 *
 * zoey stores and relays the lines without interpreting them. The vocabulary
 * belongs to the client, which refuses anything outside it.
 */

export const CLIENT_ZED = 'zed';

/** Every value `client` may take. Absent means the web page. */
export const CLIENTS: readonly string[] = [CLIENT_ZED];

/** The clients that run commands, so the only ones an answer may carry them to. */
const COMMAND_CLIENTS: readonly string[] = [CLIENT_ZED];

export const COMMANDS_MAX = 50;
export const COMMAND_MAX_LENGTH = 4000; // room for multi-line text written as escapes on the one line, e.g. a shader edit
export const COMMAND_DESCRIPTION_MAX_LENGTH = 300;
export const CONTEXT_MAX_LENGTH = 4000;

export interface Command {
  command: string; // one line of the client's command language
  description: string | null; // what running it does, in words, for the human choosing whether to
}

// A command is one line, and a description is a label: neither may carry a
// newline or any other control character a client would have to guard against.
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;

// A context is a block of lines: newlines and tabs, but still nothing else.
const CONTEXT_CONTROL_CHARS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

/** Null when `value` is an acceptable `client`, else why not. Absent is fine: the web page sends none. */
export function validateClient(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'string' || !CLIENTS.includes(value)) {
    return `client must be one of: ${CLIENTS.join(', ')}`;
  }
  return null;
}

export function clientRunsCommands(client: string | null | undefined): boolean {
  return !!client && COMMAND_CLIENTS.includes(client);
}

/**
 * Read the `context` of a question or a human reply: a short plain-text block
 * in which the client describes its own state as the human wrote (Zed: the
 * camera, the selection, the selected actor's editable rows...), so the
 * advisor can aim commands at what "this" or "here" means. Only a client that
 * runs commands sends one. Absent or blank is `context: null`; anything else
 * refused is an `error`.
 */
export function parseContext(value: unknown, client: string | null | undefined): { context: string | null; error: string | null } {
  if (value === undefined || value === null) return { context: null, error: null };
  if (typeof value !== 'string') return { context: null, error: 'context must be a string' };
  const context = value.trim();
  if (!context) return { context: null, error: null };
  if (!clientRunsCommands(client)) {
    return { context: null, error: `context can only come from a client that runs commands (client: ${COMMAND_CLIENTS.join(', ')})` };
  }
  if (context.length > CONTEXT_MAX_LENGTH) return { context: null, error: `context must be ${CONTEXT_MAX_LENGTH} characters or less` };
  if (CONTEXT_CONTROL_CHARS.test(context)) {
    return { context: null, error: 'context must be plain text: no control characters besides newlines and tabs' };
  }
  return { context, error: null };
}

/**
 * Read an agent's `commands` field. An absent or empty list is `commands:
 * null` (nothing to store); anything malformed is an `error` naming the item.
 */
export function parseCommands(value: unknown): { commands: Command[] | null; error: string | null } {
  if (value === undefined || value === null) return { commands: null, error: null };
  if (!Array.isArray(value)) return { commands: null, error: 'commands must be an array' };
  if (value.length === 0) return { commands: null, error: null };
  if (value.length > COMMANDS_MAX) return { commands: null, error: `commands may hold at most ${COMMANDS_MAX} items` };

  const commands: Command[] = [];
  for (let i = 0; i < value.length; i++) {
    const item = value[i] as Record<string, unknown> | null;
    const where = `commands[${i}]`;
    if (typeof item !== 'object' || item === null || Array.isArray(item)) {
      return { commands: null, error: `${where} must be an object with a command string` };
    }
    if (typeof item.command !== 'string' || !item.command.trim()) {
      return { commands: null, error: `${where}.command is required` };
    }
    const command = item.command.trim();
    if (command.length > COMMAND_MAX_LENGTH) {
      return { commands: null, error: `${where}.command must be ${COMMAND_MAX_LENGTH} characters or less` };
    }
    if (CONTROL_CHARS.test(command)) {
      return { commands: null, error: `${where}.command must be one line with no control characters` };
    }
    let description: string | null = null;
    if (item.description !== undefined && item.description !== null) {
      if (typeof item.description !== 'string') return { commands: null, error: `${where}.description must be a string` };
      description = item.description.trim() || null;
      if (description && description.length > COMMAND_DESCRIPTION_MAX_LENGTH) {
        return { commands: null, error: `${where}.description must be ${COMMAND_DESCRIPTION_MAX_LENGTH} characters or less` };
      }
      if (description && CONTROL_CHARS.test(description)) {
        return { commands: null, error: `${where}.description must be one line with no control characters` };
      }
    }
    commands.push({ command, description });
  }
  return { commands, error: null };
}

/** Why a list of commands cannot go in response to a message written from `client`, or null when it can. */
export function commandsRefusedFor(client: string | null | undefined, respondingTo: string): string | null {
  if (clientRunsCommands(client)) return null;
  return `commands can only answer ${respondingTo} written from a client that runs them (client: ${COMMAND_CLIENTS.join(', ')}); `
    + `this one came from ${client ? `client ${client}` : 'the web page'}`;
}

/** The stored column as the API returns it: always an array, empty for a message that carries none. */
export function readCommands(stored: unknown): Command[] {
  if (!Array.isArray(stored)) return [];
  return stored
    .filter((c): c is { command: string; description?: unknown } => typeof c === 'object' && c !== null && typeof (c as { command?: unknown }).command === 'string')
    .map((c) => ({ command: c.command, description: typeof c.description === 'string' ? c.description : null }));
}
