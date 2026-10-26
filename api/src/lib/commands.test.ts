import { describe, it, expect } from 'vitest';
import {
  validateClient, clientRunsCommands, parseContext, parseCommands, commandsRefusedFor, readCommands,
  COMMANDS_MAX, COMMAND_MAX_LENGTH, CONTEXT_MAX_LENGTH, CLIENT_ZED,
} from './commands.js';

describe('validateClient', () => {
  it('takes absent or a known client, nothing else', () => {
    expect(validateClient(undefined)).toBeNull();
    expect(validateClient(null)).toBeNull();
    expect(validateClient(CLIENT_ZED)).toBeNull();
    expect(validateClient('ZED')).toMatch(/client must be one of: zed/);
    expect(validateClient('')).toMatch(/client must be one of/);
    expect(validateClient(1)).toMatch(/client must be one of/);
  });
  it('knows which clients run commands', () => {
    expect(clientRunsCommands(CLIENT_ZED)).toBe(true);
    expect(clientRunsCommands(null)).toBe(false);
    expect(clientRunsCommands(undefined)).toBe(false);
    expect(clientRunsCommands('web')).toBe(false);
  });
});

describe('parseContext', () => {
  it('reads absent and blank as none', () => {
    expect(parseContext(undefined, CLIENT_ZED)).toEqual({ context: null, error: null });
    expect(parseContext(null, CLIENT_ZED)).toEqual({ context: null, error: null });
    expect(parseContext(' \n\t ', CLIENT_ZED)).toEqual({ context: null, error: null });
    expect(parseContext('', null)).toEqual({ context: null, error: null });
  });
  it('trims, and keeps the lines and tabs inside', () => {
    expect(parseContext('\n camera 100 200 300\nselected: BP_Rock_2\n\tRoughness\t0.5\r\n ', CLIENT_ZED))
      .toEqual({ context: 'camera 100 200 300\nselected: BP_Rock_2\n\tRoughness\t0.5', error: null });
  });
  it('comes only from a client that runs commands', () => {
    expect(parseContext('camera 100 200 300', null).error).toMatch(/context can only come from a client that runs commands \(client: zed\)/);
    expect(parseContext('camera 100 200 300', undefined).error).toMatch(/client that runs commands/);
    expect(parseContext('camera 100 200 300', 'web').error).toMatch(/client that runs commands/);
  });
  it('is a string of bounded length', () => {
    expect(parseContext(3, CLIENT_ZED).error).toBe('context must be a string');
    expect(parseContext(['camera'], CLIENT_ZED).error).toBe('context must be a string');
    expect(parseContext('x'.repeat(CONTEXT_MAX_LENGTH), CLIENT_ZED).error).toBeNull();
    expect(parseContext('x'.repeat(CONTEXT_MAX_LENGTH + 1), CLIENT_ZED).error).toMatch(/or less/);
  });
  it('refuses any control character but a newline or a tab', () => {
    for (const c of ['\u0000', '\u0007', '\u000b', '\u000c', '\u001b', '\u007f']) {
      expect(parseContext(`selected:${c} BP_Rock_2`, CLIENT_ZED).error).toMatch(/no control characters besides newlines and tabs/);
    }
    expect(parseContext('a\nb\r\nc\td', CLIENT_ZED).error).toBeNull();
  });
});

describe('parseCommands', () => {
  it('reads absent and empty as none', () => {
    expect(parseCommands(undefined)).toEqual({ commands: null, error: null });
    expect(parseCommands(null)).toEqual({ commands: null, error: null });
    expect(parseCommands([])).toEqual({ commands: null, error: null });
  });
  it('trims, and makes a missing or blank description null', () => {
    const { commands, error } = parseCommands([
      { command: '  mic set Abyss/M/MI_A.zmic scalar Roughness 0.5 ', description: ' Rougher ' },
      { command: 'mic save Abyss/M/MI_A.zmic' },
      { command: 'open Abyss/M/MI_A.zmic', description: '   ' },
    ]);
    expect(error).toBeNull();
    expect(commands).toEqual([
      { command: 'mic set Abyss/M/MI_A.zmic scalar Roughness 0.5', description: 'Rougher' },
      { command: 'mic save Abyss/M/MI_A.zmic', description: null },
      { command: 'open Abyss/M/MI_A.zmic', description: null },
    ]);
  });
  it('names the item it refuses', () => {
    expect(parseCommands('open x').error).toBe('commands must be an array');
    expect(parseCommands({ command: 'open x' }).error).toBe('commands must be an array');
    expect(parseCommands(['open x']).error).toBe('commands[0] must be an object with a command string');
    expect(parseCommands([{ command: 'open x' }, { cmd: 'open y' }]).error).toBe('commands[1].command is required');
    expect(parseCommands([{ command: '   ' }]).error).toBe('commands[0].command is required');
    expect(parseCommands([{ command: 'open x', description: 3 }]).error).toBe('commands[0].description must be a string');
  });
  it('keeps a command to one line of bounded length', () => {
    expect(parseCommands([{ command: 'open x\nstop' }]).error).toMatch(/commands\[0\]\.command must be one line/);
    // Multi-line text rides on the one line as escapes, which zoey leaves for the client to read.
    const escaped = 'open x "void main() {\\n  color = vec4(1.0);\\n}"';
    expect(parseCommands([{ command: escaped }])).toEqual({ commands: [{ command: escaped, description: null }], error: null });
    expect(parseCommands([{ command: 'open\tx' }]).error).toMatch(/one line/);
    expect(parseCommands([{ command: 'open x', description: 'a\r\nb' }]).error).toMatch(/commands\[0\]\.description must be one line/);
    expect(COMMAND_MAX_LENGTH).toBe(4000);
    expect(parseCommands([{ command: 'x'.repeat(COMMAND_MAX_LENGTH) }]).error).toBeNull();
    expect(parseCommands([{ command: 'x'.repeat(COMMAND_MAX_LENGTH + 1) }]).error).toMatch(/or less/);
  });
  it('caps the list', () => {
    const one = { command: 'open x' };
    expect(parseCommands(Array(COMMANDS_MAX).fill(one)).error).toBeNull();
    expect(parseCommands(Array(COMMANDS_MAX + 1).fill(one)).error).toMatch(/at most/);
  });
});

describe('commandsRefusedFor', () => {
  it('lets commands through only to a client that runs them', () => {
    expect(commandsRefusedFor(CLIENT_ZED, 'a question')).toBeNull();
    expect(commandsRefusedFor(null, 'a question')).toMatch(/came from the web page/);
  });
});

describe('readCommands', () => {
  it('is always an array of well-formed entries', () => {
    expect(readCommands(null)).toEqual([]);
    expect(readCommands({})).toEqual([]);
    expect(readCommands([{ command: 'open x', description: 'Open it' }, { nope: 1 }, 'open y', { command: 'open z' }])).toEqual([
      { command: 'open x', description: 'Open it' },
      { command: 'open z', description: null },
    ]);
  });
});
