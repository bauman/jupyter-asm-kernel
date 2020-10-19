from queue import Queue
from threading import Thread

from ipykernel.kernelbase import Kernel
import re
import subprocess
import tempfile
import os
import os.path as path
from pexpect import run


class RealTimeSubprocess(subprocess.Popen):
    """
    A subprocess that allows to read its stdout and stderr in real time
    """

    inputRequest = "<inputRequest>"

    def __init__(self, cmd, write_to_stdout, write_to_stderr, read_from_stdin):
        """
        :param cmd: the command to execute
        :param write_to_stdout: a callable that will be called with chunks of data from stdout
        :param write_to_stderr: a callable that will be called with chunks of data from stderr
        """
        self._write_to_stdout = write_to_stdout
        self._write_to_stderr = write_to_stderr
        self._read_from_stdin = read_from_stdin

        super().__init__(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, bufsize=0)

        self._stdout_queue = Queue()
        self._stdout_thread = Thread(target=RealTimeSubprocess._enqueue_output, args=(self.stdout, self._stdout_queue))
        self._stdout_thread.daemon = True
        self._stdout_thread.start()

        self._stderr_queue = Queue()
        self._stderr_thread = Thread(target=RealTimeSubprocess._enqueue_output, args=(self.stderr, self._stderr_queue))
        self._stderr_thread.daemon = True
        self._stderr_thread.start()

    @staticmethod
    def _enqueue_output(stream, queue):
        """
        Add chunks of data from a stream to a queue until the stream is empty.
        """
        for line in iter(lambda: stream.read(4096), b''):
            queue.put(line)
        stream.close()

    def write_contents(self):
        """
        Write the available content from stdin and stderr where specified when the instance was created
        :return:
        """

        def read_all_from_queue(queue):
            res = b''
            size = queue.qsize()
            while size != 0:
                res += queue.get_nowait()
                size -= 1
            return res

        stderr_contents = read_all_from_queue(self._stderr_queue)
        if stderr_contents:
            self._write_to_stderr(stderr_contents.decode())

        stdout_contents = read_all_from_queue(self._stdout_queue)
        if stdout_contents:
            contents = stdout_contents.decode()
            # if there is input request, make output and then
            # ask frontend for input
            start = contents.find(self.__class__.inputRequest)
            if(start >= 0):
                contents = contents.replace(self.__class__.inputRequest, '')
                if(len(contents) > 0):
                    self._write_to_stdout(contents)
                readLine = ""
                while(len(readLine) == 0):
                    readLine = self._read_from_stdin()
                # need to add newline since it is not captured by frontend
                readLine += "\n"
                self.stdin.write(readLine.encode())
            else:
                self._write_to_stdout(contents)


class ASMKernel(Kernel):
    implementation = 'jupyter_asm_kernel'
    implementation_version = '1.0'
    language = 'asm'
    language_version = 'asm'
    language_info = {'name': 'text/x-asm',
                     'mimetype': 'text/x-asm',
                     'file_extension': '.asm'}
    banner = "ASM kernel.\n" \
             "Uses yasm and gcc, creates source code files and executables in temporary folder.\n"

    def __init__(self, *args, **kwargs):
        super(ASMKernel, self).__init__(*args, **kwargs)
        self.files = []

    def cleanup_files(self):
        """Remove all the temporary files created by the kernel"""
        # keep the list of files create in case there is an exception
        # before they can be deleted as usual
        for file in self.files:
            if(os.path.exists(file)):
                os.remove(file)

    def _write_to_stdout(self, contents):
        self.send_response(self.iopub_socket, 'stream', {'name': 'stdout', 'text': contents})

    def _write_to_stderr(self, contents):
        self.send_response(self.iopub_socket, 'stream', {'name': 'stderr', 'text': contents})

    def _read_from_stdin(self):
        return self.raw_input()

    def create_jupyter_subprocess(self, cmd):
        return RealTimeSubprocess(cmd,
                                  self._write_to_stdout,
                                  self._write_to_stderr,
                                  self._read_from_stdin)

    def _filter_magics(self, code):
        magics = {
            'verbose': True,
            'compiler': "yasm",
            'linker': "gcc",
            'cflags': [],
            'ldflags': [],
            'args': []
        }
        actual_code = ''

        for line in code.splitlines():
            if line.startswith(';%'):
                key, value = line[2:].split(":", 2)
                key = key.strip().lower()
                key = key.strip()
                if key in ['ldflags', 'cflags']:
                    for flag in value.split():
                        magics[key] += [flag]
                elif key == "compiler" and value.strip() in {"yasm", "nasm"}:
                    magics["compiler"] = value.strip()
                elif key == "linker" and value.strip() in {"gcc", "ld"}:
                    magics["linker"] = value.strip()
                elif key == "verbose" and value.lower().strip() in {"false", "0"}:
                    magics["verbose"] = False
                elif key == "args":
                    # Split arguments respecting quotes
                    for argument in re.findall(r'(?:[^\s,"]|"(?:\\.|[^"])*")+', value):
                        magics['args'] += [argument.strip('"')]

            # only keep lines which did not contain magics
            else:
                actual_code += line + '\n'
        if magics['verbose']:
            self._write_to_stderr(f"[ASM kernel] "+str(magics)+"\n")
        return magics, actual_code

    def compile_code(self, source_filename, object_filename, magics):
        args = [magics["compiler"]] + magics["cflags"] + ['-o', object_filename] + [source_filename]
        str_args = " ".join(args)
        if magics['verbose']:
            self._write_to_stderr(f"[ASM kernel] Compiling with:  {str_args} \n")
        return self.create_jupyter_subprocess(args)

    def link_code(self, object_filename, binary_filename, magics):
        args = [magics["linker"]] + magics['ldflags'] + ["-o", binary_filename, object_filename]
        str_args = " ".join(args)
        if magics['verbose']:
            self._write_to_stderr(f"[ASM kernel] Linking with:    {str_args} \n")
        return self.create_jupyter_subprocess(args)

    def do_compile_link_execute(self, code):
        magics, code = self._filter_magics(code)
        with tempfile.TemporaryDirectory() as tmpdirname:
            if magics['verbose']:
                self._write_to_stderr(f"[ASM kernel] created temporary directory {tmpdirname} \n")
            source_file_name = f"{tmpdirname}/source.asm"
            with open(source_file_name, "w") as source_file:
                source_file.write(code)
            ####################################################################
            ## COMPILE
            object_file_name = f"{tmpdirname}/source.o"
            pc = self.compile_code(source_file_name, object_file_name, magics)
            while pc.poll() is None:
                pc.write_contents()
            pc.write_contents()
            if pc.returncode != 0:  # Compilation failed
                self._write_to_stderr(
                    f"[C kernel] yasm exited with code {pc.returncode}, the executable will not be executed"
                )
                return {'status': 'ok', 'execution_count': self.execution_count, 'payload': [],
                        'user_expressions': {}}
            ####################################################################
            ## Link
            binary_file_name = f"{tmpdirname}/source.run"
            pl = self.link_code(object_file_name, binary_file_name, magics)
            while pl.poll() is None:
                pl.write_contents()
            pl.write_contents()
            if pl.returncode != 0:  # Compilation failed
                self._write_to_stderr(
                    "[C kernel] gcc (linker) exited with code {}, the executable will not be executed".format(
                        pl.returncode))
                return {'status': 'ok', 'execution_count': self.execution_count, 'payload': [],
                        'user_expressions': {}}
            ####################################################################
            ## Execute
            args = [binary_file_name] + magics['args']
            str_args = " ".join(args)
            if magics['verbose']:
                self._write_to_stderr(f"[ASM kernel] Executing with:  {str_args} \n")
            (output, exit_code) = run(str_args, withexitstatus=True)
            output = output.decode(errors="backslashreplace")
            self._write_to_stdout(output)
            if magics['verbose']:
                self._write_to_stderr(f"[ASM kernel] Executable exited with code {exit_code} \n")
        return {'status': 'ok', 'execution_count': self.execution_count, 'payload': [], 'user_expressions': {}}

    def do_execute(self, code, silent, store_history=True,
                   user_expressions=None, allow_stdin=True):
        try:
            output = self.do_compile_link_execute(code)
        except Exception as err:
            self._write_to_stderr(f"[ASM kernel] Error {err} \n")
            output = {'status': 'ok', 'execution_count': self.execution_count, 'payload': [], 'user_expressions': {}}
        return output

    def do_shutdown(self, restart):
        """Cleanup the created source code files and executables when shutting down the kernel"""
        self.cleanup_files()
