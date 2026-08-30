/**
 * Start one vendorfake per vendor before the suite and stop it after.
 *
 * Two ways to run it, chosen by the environment:
 *
 *   VENDORFAKE_IMAGE=vendorfake:verify npm test   -> the container, via testcontainers
 *   npm test                                       -> `vendorfake serve` as a child process
 *
 * The container is what CI should run; the child process is for a laptop
 * without Docker, and needs `vendorfake` installed in some Python
 * (VENDORFAKE_PYTHON, else the repository's .venv, else python3 on PATH).
 *
 * Also started here: a webhook receiver. Vitest runs test files in worker
 * processes, so a receiver started in a test could not be reached by a
 * container started here; instead this process listens and appends every
 * delivery -- headers and the raw body, base64 -- to a JSONL file the tests
 * read. In container mode the fake reaches it through testcontainers' host
 * port forwarding (`host.testcontainers.internal`).
 */

import { spawn, type ChildProcess } from "node:child_process";
import { appendFileSync, existsSync, mkdtempSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { GenericContainer, TestContainers, Wait, type StartedTestContainer } from "testcontainers";
import type { TestProject } from "vitest/node";

const VENDORS = ["square", "clover"] as const;
type Vendor = (typeof VENDORS)[number];

declare module "vitest" {
  export interface ProvidedContext {
    vendorfake: Record<Vendor, string>;
    /** The receiver's URL as the fake sees it. */
    receiverUrl: string;
    /** Where the receiver writes what it got, one JSON object per line. */
    receiverLog: string;
    mode: "container" | "subprocess";
  }
}

export default async function setup(project: TestProject): Promise<() => Promise<void>> {
  const image = process.env.VENDORFAKE_IMAGE;
  const mode = image ? "container" : "subprocess";
  const log = join(mkdtempSync(join(tmpdir(), "vendorfake-receiver-")), "deliveries.jsonl");
  const receiver = await startReceiver(log);
  const stops: Array<() => Promise<void>> = [async () => new Promise((done) => receiver.server.close(() => done()))];
  const urls = {} as Record<Vendor, string>;

  let receiverHost = "127.0.0.1";
  if (mode === "container") {
    // Must happen before the containers start: it is what puts
    // `host.testcontainers.internal` in their /etc/hosts.
    await TestContainers.exposeHostPorts(receiver.port);
    receiverHost = "host.testcontainers.internal";
  }

  for (const vendor of VENDORS) {
    const started = mode === "container" ? await startContainer(image!, vendor) : await startSubprocess(vendor);
    urls[vendor] = started.url;
    stops.push(started.stop);
    console.log(`[vendorfake] ${vendor} (${mode}) at ${started.url}`);
  }

  project.provide("vendorfake", urls);
  project.provide("receiverUrl", `http://${receiverHost}:${receiver.port}/webhooks`);
  project.provide("receiverLog", log);
  project.provide("mode", mode);

  return async () => {
    for (const stop of stops.reverse()) await stop();
  };
}

// ---------------------------------------------------------------------------

interface Started {
  url: string;
  stop: () => Promise<void>;
}

async function startContainer(image: string, vendor: Vendor): Promise<Started> {
  const container: StartedTestContainer = await new GenericContainer(image)
    .withEnvironment({ VENDORFAKE_VENDOR: vendor })
    .withExposedPorts(8080)
    .withWaitStrategy(Wait.forHttp("/__unit/health", 8080).forStatusCode(200))
    .start();
  return {
    url: `http://${container.getHost()}:${container.getMappedPort(8080)}`,
    stop: async () => {
      await container.stop();
    },
  };
}

function pythonExecutable(): string {
  if (process.env.VENDORFAKE_PYTHON) return process.env.VENDORFAKE_PYTHON;
  const checkout = resolve(import.meta.dirname, "../../../.venv/bin/python");
  return existsSync(checkout) ? checkout : "python3";
}

async function startSubprocess(vendor: Vendor): Promise<Started> {
  const child: ChildProcess = spawn(
    pythonExecutable(),
    ["-m", "vendorfake", "serve", "--vendor", vendor, "--host", "127.0.0.1", "--port", "0", "--log-level", "error"],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  const url = await new Promise<string>((resolveUrl, reject) => {
    let stdout = "";
    let stderr = "";
    child.stdout!.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
      const found = /listening on (http:\/\/[^\s]+)/.exec(stdout);
      if (found) resolveUrl(found[1]);
    });
    child.stderr!.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.on("exit", (code) => reject(new Error(`vendorfake serve --vendor ${vendor} exited with ${code}:\n${stderr}`)));
    child.on("error", reject);
  });
  return {
    url,
    stop: () =>
      new Promise((done) => {
        child.once("exit", () => done());
        child.kill("SIGTERM");
      }),
  };
}

async function startReceiver(log: string): Promise<{ server: Server; port: number }> {
  const server = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on("data", (chunk: Buffer) => chunks.push(chunk));
    request.on("end", () => {
      const record = {
        path: request.url,
        headers: request.headers,
        bodyBase64: Buffer.concat(chunks).toString("base64"),
      };
      appendFileSync(log, JSON.stringify(record) + "\n");
      response.writeHead(200, { "content-length": "0" }).end();
    });
  });
  await new Promise<void>((done) => server.listen(0, "0.0.0.0", done));
  const address = server.address();
  if (address === null || typeof address === "string") throw new Error("receiver did not bind a port");
  return { server, port: address.port };
}
