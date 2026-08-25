import { spawn, type ChildProcess } from 'node:child_process';
import { createServer } from 'node:net';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Launch the unit the way a CONSUMER would, and hand back a base URL.
 *
 * Two backends behind one contract:
 *
 *   docker   Testcontainers starts the published image. This is the real
 *            packaging story and what CI runs.
 *   process  The built server is spawned directly. Same image contents, same
 *            entry point, same environment contract — no container runtime.
 *
 * `auto` (the default) picks docker when a container runtime is reachable and
 * falls back to process otherwise, and every test prints which backend ran, so
 * a green result is never ambiguous about what it proved.
 *
 * The second backend is not a workaround for a missing runtime: it is what lets
 * the same consumer-facing assertions run in a sub-second edit loop and in the
 * container CI publishes, instead of a team maintaining two different tests and
 * discovering the drift between them in production.
 */
export type Backend = 'docker' | 'process';

export interface UnitHandle {
  baseUrl: string;
  backend: Backend;
  describe: string;
  /**
   * URL the UNIT can use to reach a server listening on the test host. Direct
   * loopback for a spawned process; the Testcontainers host alias for a
   * container, which cannot see the host's 127.0.0.1.
   */
  hostUrl(port: number): string;
  stop(): Promise<void>;
}

export interface LaunchOptions {
  profile?: string;
  env?: Record<string, string>;
  backend?: Backend | 'auto';
  /** Host ports the unit must be able to reach, e.g. a webhook subscriber. */
  exposeHostPorts?: number[];
}

const here = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(here, '..', '..');
const SERVER_ENTRY = resolve(REPO_ROOT, 'packages/square/dist/bin/serve.js');

export async function detectBackend(): Promise<Backend> {
  const forced = process.env.UNIT_TEST_BACKEND;
  if (forced === 'docker' || forced === 'process') return forced;
  return (await dockerAvailable()) ? 'docker' : 'process';
}

/**
 * Find a container runtime, and configure Testcontainers for it.
 *
 * Not every developer's Docker lives at /var/run/docker.sock: Colima, Rancher
 * Desktop and Podman each put it somewhere else, and Testcontainers only probes
 * the well-known paths. Asking the docker CLI which context is active turns
 * "works on my machine" into "works on the machine that has docker installed",
 * which is the whole point of shipping a container in the first place.
 */
async function dockerAvailable(): Promise<boolean> {
  const { access } = await import('node:fs/promises');
  if (process.env.DOCKER_HOST) return true;

  for (const socket of ['/var/run/docker.sock', `${process.env.HOME ?? ''}/.docker/run/docker.sock`, '/run/podman/podman.sock']) {
    try {
      await access(socket);
      return true;
    } catch {
      // keep looking
    }
  }

  const endpoint = await activeDockerEndpoint();
  if (!endpoint?.startsWith('unix://')) return false;
  const path = endpoint.slice('unix://'.length);
  try {
    await access(path);
  } catch {
    return false;
  }
  process.env.DOCKER_HOST = endpoint;
  // Ryuk mounts the socket from INSIDE the runtime's VM, where it is always at
  // the canonical path even when the host sees it elsewhere.
  process.env.TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE ??= '/var/run/docker.sock';
  return true;
}

async function activeDockerEndpoint(): Promise<string | null> {
  const { execFile } = await import('node:child_process');
  return new Promise((resolvePath) => {
    execFile('docker', ['context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], { timeout: 10_000 }, (err, stdout) => {
      resolvePath(err ? null : stdout.trim() || null);
    });
  });
}

export async function launchUnit(opts: LaunchOptions = {}): Promise<UnitHandle> {
  const backend = opts.backend && opts.backend !== 'auto' ? opts.backend : await detectBackend();
  const env = { UNIT_PROFILE: opts.profile ?? 'full', UNIT_LOG_LEVEL: 'warn', ...(opts.env ?? {}) };
  return backend === 'docker' ? startContainer(env, opts.exposeHostPorts ?? []) : startProcess(env);
}

async function startContainer(env: Record<string, string>, exposeHostPorts: number[]): Promise<UnitHandle> {
  // Runs even when the backend was forced with UNIT_TEST_BACKEND=docker:
  // resolving the socket is a precondition of using a container, not a step in
  // deciding whether to.
  await dockerAvailable();

  // Imported lazily so the process backend never pays for the docker client.
  const { GenericContainer, TestContainers, Wait } = await import('testcontainers');
  const tag = process.env.UNIT_IMAGE ?? 'vendor-unit-square:test';

  // Must happen before the container starts, or the alias is not resolvable.
  if (exposeHostPorts.length > 0) await TestContainers.exposeHostPorts(...exposeHostPorts);

  // CI builds the image once and passes UNIT_IMAGE; a bare local run builds it.
  const image = tag;
  if (!process.env.UNIT_IMAGE) {
    await GenericContainer.fromDockerfile(REPO_ROOT).build(tag, { deleteOnExit: false });
  }

  const container = await new GenericContainer(image)
    .withExposedPorts(8080)
    .withEnvironment(env)
    .withWaitStrategy(Wait.forHttp('/__unit/health', 8080).forStatusCode(200))
    .withStartupTimeout(120_000)
    .start();

  const baseUrl = `http://${container.getHost()}:${container.getMappedPort(8080)}`;
  return {
    baseUrl,
    backend: 'docker',
    describe: `Testcontainers, image ${image}`,
    hostUrl: (port: number) => `http://host.testcontainers.internal:${port}`,
    stop: async () => {
      await container.stop();
    },
  };
}

async function startProcess(env: Record<string, string>): Promise<UnitHandle> {
  const port = await freePort();
  const child: ChildProcess = spawn(process.execPath, [SERVER_ENTRY], {
    env: { ...process.env, ...env, UNIT_PORT: String(port), UNIT_HOST: '127.0.0.1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const baseUrl = `http://127.0.0.1:${port}`;
  const stderr: string[] = [];
  child.stderr?.on('data', (c: Buffer) => stderr.push(c.toString()));

  try {
    await waitForHealth(baseUrl, 20_000);
  } catch (err) {
    child.kill('SIGKILL');
    throw new Error(`unit did not become healthy: ${String(err)}\n${stderr.join('')}`);
  }

  return {
    baseUrl,
    backend: 'process',
    describe: `spawned ${SERVER_ENTRY}`,
    hostUrl: (hostPort: number) => `http://127.0.0.1:${hostPort}`,
    stop: async () => {
      child.kill('SIGTERM');
      await new Promise<void>((r) => {
        const t = setTimeout(() => {
          child.kill('SIGKILL');
          r();
        }, 3000);
        child.once('exit', () => {
          clearTimeout(t);
          r();
        });
      });
    },
  };
}

async function waitForHealth(baseUrl: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let last: unknown;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl}/__unit/health`);
      if (res.ok) return;
      last = `status ${res.status}`;
    } catch (e) {
      last = e;
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error(`timed out waiting for ${baseUrl}/__unit/health (last: ${String(last)})`);
}

function freePort(): Promise<number> {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close(() => resolvePort(port));
    });
  });
}

/** Small JSON client so the tests read like a consumer's code. */
export async function call<T = unknown>(
  baseUrl: string,
  method: string,
  path: string,
  init: { body?: unknown; headers?: Record<string, string> } = {},
): Promise<{ status: number; headers: Record<string, string>; body: T; text: string }> {
  const res = await fetch(`${baseUrl}${path}`, {
    method,
    headers: { ...(init.body !== undefined ? { 'content-type': 'application/json' } : {}), ...(init.headers ?? {}) },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    redirect: 'manual',
  });
  const text = await res.text();
  let body: T;
  try {
    body = (text ? JSON.parse(text) : undefined) as T;
  } catch {
    body = text as unknown as T;
  }
  return { status: res.status, headers: Object.fromEntries(res.headers), body, text };
}
