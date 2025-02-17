#!/usr/bin/python3

# Copyright (C) 2020 Julian Valentin, LTeX Development Community
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import argparse
import datetime
import gzip
import io
import json
import os
import pathlib
import random
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Dict, IO, Optional, Tuple
import urllib.request



class LSPClient(object):
  def __init__(self, conn: socket.socket, stdout: IO[bytes], stderr: IO[bytes]) -> None:
    self.conn = conn
    self.stdout = stdout
    self.stderr = stderr
    self.stdoutPosition = 0
    self.stderrPosition = 0
    self.requestCounter = 0
    self.documentCounter = 0

  @staticmethod
  def wrap_message(body: str) -> bytes:
    encodedBody = body.encode()
    header = "Content-Length: {}\r\n".format(len(encodedBody)).encode()
    return header + b"\r\n" + encodedBody

  def read_response(self) -> Any:
    header = b""
    while not header.endswith(b"\r\n\r\n"): header += self.conn.recv(1)
    headers: Dict[str, str] = dict([(x.split(": ")[0], x.split(": ")[1])
        for x in header.decode()[:-4].split("\r\n")])
    lengthBody = int(headers["Content-Length"])

    body = b""
    while len(body) < lengthBody: body += self.conn.recv(lengthBody - len(body))
    response = json.loads(body)

    return response

  def listen_for_response(self, requestId: int) -> Any:
    response = {"id" : None}

    while response["id"] != requestId:
      response = self.read_response()
      if "method" in response: self.process_notification(response)
      elif response["id"] != requestId: self.process_response(response)

    return response

  def listen_for_notification(self, uri: Optional[str] = None) -> Any:
    while True:
      response = self.read_response()

      if (uri is None) or (("uri" in response["params"]) and (response["params"]["uri"] == uri)):
        break

    return response

  def process_response(self, response: Any) -> None:
    print("Received response: {}".format(response))

  def process_notification(self, notification: Any) -> None:
    print("Received notification: {}".format(notification))

  @staticmethod
  def indent_output(output: str) -> str:
    if len(output) == 0: return ""
    indentation = 4 * " "
    indentedOutput = indentation + output.replace("\n", "\n" + indentation)
    if output.endswith("\n"): indentedOutput = indentedOutput[:-len(indentation)]
    return indentedOutput

  def print_output(self) -> Tuple[str, str]:
    self.stdout.seek(self.stdoutPosition)
    stdoutOutput = self.stdout.read().decode()
    print(LSPClient.indent_output(stdoutOutput), end="")
    self.stdout.seek(0, io.SEEK_END)
    self.stdoutPosition = self.stdout.tell()

    self.stderr.seek(self.stderrPosition)
    stderrOutput = self.stderr.read().decode()
    print(LSPClient.indent_output(stderrOutput), file=sys.stderr, end="")
    self.stderr.seek(0, io.SEEK_END)
    self.stderrPosition = self.stderr.tell()

    return stdoutOutput, stderrOutput

  def send_request(self, method: str, params: Any, verbose: bool = True,
        failOnStderrOutput: bool = True) -> Any:
    self.print_output()
    self.requestCounter += 1
    requestId = self.requestCounter

    body = json.dumps({"jsonrpc" : "2.0", "id" : requestId, "method" : method, "params" : params})
    lspRequest = LSPClient.wrap_message(body)
    if verbose: print("Sending request: {}".format(body))
    self.conn.send(lspRequest)

    startTime = datetime.datetime.now()
    response = self.listen_for_response(requestId)
    duration = (datetime.datetime.now() - startTime).total_seconds()
    stdoutOutput, stderrOutput = self.print_output()

    if verbose: print("Received response after {:.1f}s: {}".format(duration, response))

    if failOnStderrOutput and (len(stderrOutput) > 0):
      raise RuntimeError("Detected output on stderr.")

    return response

  def send_notification(self, method: str, params: Any, verbose: bool = True) -> None:
    self.print_output()

    body = json.dumps({"jsonrpc" : "2.0", "method" : method, "params" : params})
    lspNotification = LSPClient.wrap_message(body)
    if verbose: print("Sending notification: {}".format(body))
    self.conn.send(lspNotification)

  def validate_document(self, text: str, verbose: bool = False, failOnStderrOutput: bool = True,
        path: Optional[str] = None) -> None:
    self.print_output()
    self.documentCounter += 1
    uri = "foo://{}".format(self.documentCounter)

    if not verbose:
      print("Checking {} with {} characters...".format(
          (path if path is not None else "document"), len(text)))

    self.send_notification("textDocument/didOpen", {
          "textDocument" : {"uri" : uri, "languageId" : "latex", "version" : 1, "text" : text},
        }, verbose=verbose)

    startTime = datetime.datetime.now()
    notification = self.listen_for_notification(uri)
    duration = (datetime.datetime.now() - startTime).total_seconds()
    stdoutOutput, stderrOutput = self.print_output()

    if verbose:
      print("Received notification after {:.1f}s: {}".format(duration, notification))
    else:
      print("Obtained {} rule matches after {:.1f}s.".format(
          len(notification["params"]["diagnostics"]), duration))

    if failOnStderrOutput and (len(stderrOutput) > 0):
      raise RuntimeError("Detected output on stderr.")



def processArxivTex(lspClient: LSPClient, tex: str, texPath: str, arxivId: str,
      saveTex: Optional[str]) -> None:
  if saveTex is not None:
    saveTexPath = os.path.join(saveTex, (
        "{}.tex".format(arxivId) if os.path.basename(texPath) == arxivId else
        "{}_{}".format(arxivId, os.path.basename(texPath))))
    print("Saving LaTeX file as: {}".format(saveTexPath))
    with open(saveTexPath, "w") as f: f.write(tex)

  lspClient.validate_document(tex, path=texPath)



def main() -> None:
  parser = argparse.ArgumentParser(description=
      "Test LTeX VS Code extension on randomly chosen arXiv papers")
  parser.add_argument("--batch-size", type=int, default=10, help=
      "Number of arXiv papers to check")
  parser.add_argument("--port", type=int, default=0, help=
      "Port to use for the communication with the language server. "
      "If omitted, use an arbitrary open port.")
  parser.add_argument("--save-tex", type=str, help=
      "Save checked LaTeX files in the specified directory")
  parser.add_argument("--seed", type=int, help=
      "Use a specific seed to generate arXiv IDs. If omitted, use a random seed.")
  args = parser.parse_args()

  extensionsPath = os.path.join(pathlib.Path.home(), ".vscode", "extensions")
  ltexMainPaths = [x for x in os.listdir(extensionsPath)
      if x.startswith("valentjn.vscode-ltex-") and (x.count("-") == 2)]
  ltexLanguagePaths = [x for x in os.listdir(extensionsPath)
      if x.startswith("valentjn.vscode-ltex-en-")]

  versionSortKey = (lambda x: [int(y) for y in x.split("-")[-1].split(".")])
  ltexMainPaths.sort(key=versionSortKey)
  ltexLanguagePaths.sort(key=versionSortKey)

  if len(ltexMainPaths) == 0:
    raise RuntimeError("No LTeX main extension found.")
  if len(ltexLanguagePaths) == 0:
    raise RuntimeError("No LTeX English language extension found.")

  ltexMainPath, ltexLanguagePath = ltexMainPaths[-1], ltexLanguagePaths[-1]
  print("Using LTeX extension from: {}".format(
        os.path.join(extensionsPath, ltexMainPath)))
  print("Using LTeX English language extension from: {}".format(
        os.path.join(extensionsPath, ltexLanguagePath)))

  host, port = "localhost", args.port
  addressInfo = socket.getaddrinfo(host, port)[0]
  sock = socket.socket()
  sock.bind(addressInfo[4])
  port = sock.getsockname()[1]
  print("Using port {}.".format(port))

  ltexStdout = tempfile.TemporaryFile("w+b")
  ltexStderr = tempfile.TemporaryFile("w+b")
  ltexArgs = ["java", "-classpath", os.pathsep.join([
        os.path.join(extensionsPath, ltexMainPath, "lib", "languagetool-languageserver",
          "build", "install", "languagetool-languageserver", "lib", "ltex-ls-languagetool-patch.jar"),
        os.path.join(extensionsPath, ltexMainPath, "lib", "languagetool-languageserver",
          "build", "install", "languagetool-languageserver", "lib", "*"),
        os.path.join(extensionsPath, ltexLanguagePath,  "lib", "*"),
      ]), "LanguageToolLanguageServerLauncher", str(port)]
  print("Starting LanguageTool language server...")
  process = subprocess.Popen(ltexArgs, stdout=ltexStdout, stderr=ltexStderr)

  sock.listen()
  time.sleep(1)
  conn, addr = sock.accept()

  lspClient = LSPClient(conn, ltexStdout, ltexStderr)

  lspClient.send_request("initialize", {
        "processId" : os.getpid(), "rootUri" : None, "capabilities" : {},
      }, failOnStderrOutput=False)

  #lspClient.send_notification("workspace/didChangeConfiguration", {
  #      "settings" : {"ltex" : {"enabled" : True, "language" : "en-US"}},
  #    })

  seed = args.seed
  if seed is None: seed = random.randrange(1000000)
  print("Using seed {}.".format(seed))
  random.seed(seed)

  for i in range(args.batch_size):
    year = 18
    month = random.randrange(1, 13)
    number = random.randrange(5000)
    arxivId = "{:02}{:02}.{:05}".format(year, month, number)
    print("")
    print("Processing arXiv paper {}...".format(arxivId))

    with tempfile.TemporaryDirectory() as tempDir:
      response = urllib.request.urlopen("https://arxiv.org/e-print/{}".format(arxivId))
      tar = response.read()

      if response.info().get("Content-Encoding") == "x-gzip": tar = gzip.decompress(tar)

      tarPath = os.path.join(tempDir, arxivId)
      with open(tarPath, "wb") as f: f.write(tar)

      if tarfile.is_tarfile(tarPath):
        with tarfile.open(tarPath) as tarFile:
          if any((".." in x) or x.startswith("/") for x in tarFile.getnames()):
            print(("Skipping arXiv paper {} due to suspicious path names in "
                "tar archive.").format(arxivId))
            continue

          tarFile.extractall(tempDir)

          for root, dirs, files in os.walk(tempDir):
            for file_ in sorted(files):
              if file_.endswith(".tex"):
                texPath = os.path.join(root, file_)

                try:
                  with open(texPath, "r") as texFile: tex = texFile.read()
                except UnicodeDecodeError:
                  print("Skipping LaTeX file {} due to Unicode decode error.".format(texPath))
                  continue

                processArxivTex(lspClient, tex, texPath, arxivId, args.save_tex)
      else:
        texPath = tarPath

        try:
          tex = tar.decode()
        except UnicodeDecodeError:
          print("Skipping LaTeX file {} due to Unicode decode error.".format(texPath))
          continue

        processArxivTex(lspClient, tex, texPath, arxivId, args.save_tex)

    time.sleep(5)



if __name__ == "__main__":
  main()
