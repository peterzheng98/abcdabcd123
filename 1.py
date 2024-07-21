import docker
import os
import uuid
import logging
import sys
import time
import queue
import multiprocessing
import re
import subprocess
import shutil

# base path
repo_archive_path = '/srv/samba/backup/compiler-judge/archive'
repo_build_root_path = '/srv/samba/backup/compiler-judge/temp'
log_path = '/srv/samba/backup/compiler-judge/log/{}'.format(sys.argv[1])

# dockerfile template: from {} but copy all files in the repo to /compiler and then build and tag
dockerfile_template = '''FROM {}

COPY . /compiler
RUN cd /compiler && make build
ENTRYPOINNT ["make", "run"]
'''

# for semantic
semantic_check_root = '' # the root path for semantic check
semantic_check_lib  = '.txt' # the lib for semantic check

# for codegen
ravel_bin_root = ''
codegen_check_root = '' # the root path for semantic check
codegen_check_lib  = '.txt' # the lib for semantic check

# set logging with date time, and both write to files
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=log_path,
                    filemode='a')


def build_image_worker(path, tag, rm, container_limits, network_mode, q, user_id, url):
    client = docker.from_env()
    try:
        user_image = client.images.build(path=path, tag=tag, rm=rm, container_limits=container_limits, network_mode=network_mode)
        message = ''
        for chunk in user_image[1]:
            if 'stream' in chunk:
                message = message + chunk['stream'].strip() + '\n'
            elif 'errorDetail' in chunk:
                message = message + chunk['errorDetail']['message'].strip() + '\n'
        q.put((0, message))
        logging.info('[worker]build docker image success {} (request {}, url {})'.format(user_image[0].id, user_id, url))
    except docker.errors.BuildError as e:
        err_message = ''
        for line in e.build_log:
            if 'stream' in line:
                err_message += (line['stream'].strip() + '\n')
            elif 'errorDetail' in line:
                err_message += (line['errorDetail']['message'].strip() + '\n')
        q.put((1, err_message))
        logging.error('[worker]build docker image failed {} (request {}, url {})'.format(e, user_id, url))
    except Exception as e:
        logging.error('[worker]build docker image general failed {} (request {}, url {})'.format(e, user_id, url))
        q.put((2, ''))

def extract_input_output_exitcode_verdict(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        verdict = 'Verdict: Success\n' in file.readlines()
        content = file.read()
    input_regex = r'Input:\n=== input ===\n(.*?)\n=== end ==='
    output_regex = r'Output:\n=== output ===\n(.*?)\n=== end ==='
    exitcode_regex = r'ExitCode: (.+)'
    input_match = re.search(input_regex, content, re.DOTALL)
    output_match = re.search(output_regex, content, re.DOTALL)
    exitcode_match = re.search(exitcode_regex, content)
    if input_match:
        input_data = input_match.group(1).strip()
    else:
        input_data = ""
    if output_match:
        output_data = output_match.group(1).strip()
    else:
        output_data = ""
    if exitcode_match:
        exitcode = exitcode_match.group(1).strip()
    else:
        exitcode = ""
    return content, input_data, output_data, exitcode, verdict

def run_semantic(path, tag, q, user_id, url, test_path, testcase):
    client = docker.from_env()
    content, input_data, output_data, exitcode, verdict = extract_input_output_exitcode_verdict(test_path)
    try:
        process = subprocess.Popen(
            ['docker', 'run', '--rm', '-i', tag],
            cwd=path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # shell=True
        )
        stdout, stderr = process.communicate(input=content)
        if (process.returncode == 0) == verdict:
            statue = 'AC'
        else:
            statue = 'WA'
        q.put((0, statue))
        logging.info('[worker]run semantic check success {} statu:{} (request {}, url {})'.format(testcase, statue, user_id, url))
    except Exception as e:
        q.put((2, ''))
        logging.error('[worker]run semantic check failed {} error:{} (request {}, url {})'.format(testcase, e, user_id, url))

def run_codegen(path, tag, q, user_id, url, test_path, testcase):
    client = docker.from_env()
    content, input_data, output_data, exitcode, verdict = extract_input_output_exitcode_verdict(test_path)
    try:
        process = subprocess.Popen(
            ['docker', 'run', '--rm', '-i', tag],
            cwd=path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # shell=True
        )
        stdout, stderr = process.communicate(input=content)
        if process.returncode == 0:
            statue = 'AC'
            logging.info('[worker]run codegen semantic check success {} statu:{} (request {}, url {})'.format(testcase, statue, user_id, url))
            ravel_user_testspace = os.path.join(ravel_bin_root, user_id)
            with open(os.path.join(ravel_user_testspace, 'test.s'), "w") as f_ravel:
                f_ravel.write(stdout)
                f_ravel.flush()
            with open(os.path.join(ravel_user_testspace, 'test.in'), "w") as f_ravel:
                f_ravel.write(input_data)
                f_ravel.flush()
            process2 = subprocess.Popen(
                f'./ravel_test --input-file=./{user_id}/test.in --output-file=./{user_id}/test.out ./{user_id}/test.s ./{user_id}/builtin.s',
                cwd=ravel_bin_root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # shell=True
            )
            stdout, stderr = process2.communicate()
            # TODO: 
            q.put((0, statue))
        else:
            statue = 'WA'
            q.put((1, statue))
            logging.info('[worker]run codegen semantic check wrong {} statu:{} (request {}, url {})'.format(testcase, statue, user_id, url))
    except Exception as e:
        q.put((2, ''))
        logging.error('[worker]run codegen semantic check failed {} error:{} (request {}, url {})'.format(testcase, e, user_id, url))

def run_compile(user_id, url, docker_id):
    # read available image from file: docker_image_list
    with open('/srv/samba/backup/compiler-judge/docker_image_list', 'r') as f:
        image_list = f.read().split('\n')
        image_list_new = []
        for i in image_list:
            if len(i) != 0:
                image_list_new.append(i)
        image_list = image_list_new
    # check if the image is available
    if docker_id not in image_list:
        logging.error('docker image {} is not available (request {}, url {})'.format(docker_id, user_id, url))
        return False
    
    # generate a new uuid for this compile
    compile_id = str(uuid.uuid4())
    # generate a new path for this compile and write to log
    compile_path = os.path.join(repo_build_root_path, compile_id)
    # create corresponding folder with exception check
    try:
        os.makedirs(compile_path)
    except Exception as e:
        logging.error('create compile path failed: {} (request {}, url {})'.format(compile_path, user_id, url))
        return False
    logging.info('local compile path: {} (request {}, url {})'.format(compile_path, user_id, url))
    # change dir to compile_path
    os.chdir(compile_path)
    # clone the repo
    os.system('git clone {} compiler'.format(url))
    # check if the repo is cloned
    if not os.path.exists(os.path.join(compile_path, 'compiler')):
        logging.error('clone repo failed (request {}, url {})'.format(user_id, url))
        return False
    
    # zip the repo and rename to {user_id}+timestamp
    repo_archive_name = '{}-{}.zip'.format(user_id, str(int(time.time())))
    # zip files to archive names
    os.system('zip -r {} {}'.format(os.path.join(repo_archive_path, repo_archive_name), compile_path))
    
    # write Dockerfile to compile_path
    docker_string = dockerfile_template.join(docker_id)
    with open(os.path.join(compile_path, 'Dockerfile'), 'w') as f:
        f.write(docker_string)
    
    # enter the compile_path, build a docker container and run it
    os.chdir(compile_path)
    # use multiprocess to build image and limit execution time to 30
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=build_image_worker, args=(compile_path, user_id + ':latest', True, {'memory': '1024m'}, 'none', q, user_id, url))
    p.start()
    p.join(30)
    if p.is_alive():
        p.terminate()
        p.join()
        logging.error('build docker image timeout (request {}, url {})'.format(user_id, url))
        return False
    else:
        result = q.get()
        if result[0] != 0:
            logging.error('build docker image failed (request {}, url {})'.format(user_id, url))
            return False
        else:
            logging.info('build docker image success (request {}, url {})'.format(user_id, url))
            # return True
    
    semantic_testcases = open(semantic_check_lib, 'r').readlines()
    semantic_ans = []
    for testcase in semantic_testcases:
        test_path = os.path.join(semantic_check_root, testcase)
        p = multiprocessing.Process(target=run_semantic, args=(compile_path, user_id + ':latest', q, user_id, url, test_path, testcase))
        p.start()
        p.join(15)
        if p.is_alive():
            p.terminate()
            p.join()
            logging.error('run semantic check timeout {} (request {}, url {})'.format(testcase, user_id, url))
            semantic_ans.append((False, 'TLE'))
        else:
            result = q.get()
            if result[0] != 0:
                logging.error('run semantic check failed (request {}, url {})'.format(user_id, url))
                semantic_ans.append((False, 'RE'))
            else:
                logging.info('run semantic check success (request {}, url {})'.format(user_id, url))
                semantic_ans.append((result[1] == 'AC', result[1]))
    
    codegen_testcases = open(codegen_check_lib, 'r').readlines()
    codegen_ans = []
    ravel_user_testspace = os.path.join(ravel_bin_root, user_id)
    if not os.path.exists(ravel_user_testspace):
        os.mkdir(ravel_user_testspace)
    builtin_path = os.path.join(os.path.join(compile_path, 'compiler'), 'builtin.s')
    if os.path.exists(builtin_path):
        shutil.copy(builtin_path, os.path.join(ravel_user_testspace, 'builtin.s'))

    for testcase in codegen_testcases:
        test_path = os.path.join(codegen_check_root, testcase)
