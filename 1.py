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
import datetime

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

import mysql.connector

def create_mysql_connection(host_name, user_name, user_password, db_name):
    connection = None
    try:
        connection = mysql.connector.connect(
            host=host_name,
            user=user_name,
            passwd=user_password,
            database=db_name
        )
        logging.info("mysql connect successfully")
    except Exception as e:
        logging.error(f"mysql connect error: {e}")
    return connection

def execute_query(connection, query, data, info=""):
    cursor = connection.cursor()
    try:
        cursor.execute(query, data)
        connection.commit()
        logging.info(f"Query {info} execute successfully")
        return cursor.lastrowid
    except Exception as e:
        logging.error(f"Query {info} execute error: {e} in {query}")
        return None

def select_uid_from_stuid(connection, stuid):
    select_uid_from_stuid_query = "SELECT user_id FROM compiler.Users WHERE stuid = %s"
    cursor = connection.cursor()
    cursor.execute(select_uid_from_stuid_query, (stuid, ))
    result = cursor.fetchone()
    if result:
        return result[0]
    else:
        logging.error(f"error in fetch user_id for stu_id {stuid}")
        return None

def create_a_submit(connection, uid, stuid, git_repo):
    insert_submit_query = """
INSERT INTO compiler.JudgeAttempts (
    user_id, problem_id, submission_time, status, githash, git_message, attempt_hash, stuid
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
    submit_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    data = (uid, None, submit_timestamp, 1, None, git_repo, None, stuid)
    return execute_query(connection, insert_submit_query, data, info="create a new submit"), submit_timestamp

def update_submit_status(connection, attempt_id, item, value):
    update_query = f"UPDATE compiler.JudgeAttempts SET {item} = %s WHERE attempt_id = %s"
    data = (value, attempt_id)
    execute_query(connection, update_query, data, info=f"update {attempt_id} {item} = {value}")

def fetch_testcase_by_phase(connection, phase_id):
    phase_query = "SELECT * FROM compiler.TestCases WHERE problem_phase = %s"
    cursor = connection.cursor()
    cursor.execute(phase_query, (phase_id, ))
    result = cursor.fetchall()
    return result

def create_a_testcase_result(connection, attempt_id, testcase, is_correct, compile_result,
                             compile_error_="", execute_output_="", cycle_count=0, mem_usage=0, phase_id=0, compile_time=0.0, submit_time="", user_id=""):
    insert_result_query = """
INSERT INTO compiler.TestCaseResults (
    attempt_id, test_case_id, disp_value, is_correct, compile_result, compile_error_base64, execute_output_base64, cycle_count, memory_usage, phase_id, compile_time, submit_time, submit_judger
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
    data = (attempt_id, testcase['test_case_id'], testcase['test_case_disp_name'], is_correct, compile_result, compile_error_, execute_output_, cycle_count, mem_usage, phase_id, compile_time, submit_time, user_id)
    return execute_query(connection, insert_result_query, data, info="create a new result")

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
    # with open(file_path, 'r', encoding='utf-8') as file:
    #     verdict = 'Verdict: Success\n' in file.readlines()
    #     content = file.read()
    # input_regex = r'Input:\n=== input ===\n(.*?)\n=== end ==='
    # output_regex = r'Output:\n=== output ===\n(.*?)\n=== end ==='
    # exitcode_regex = r'ExitCode: (.+)'
    # input_match = re.search(input_regex, content, re.DOTALL)
    # output_match = re.search(output_regex, content, re.DOTALL)
    # exitcode_match = re.search(exitcode_regex, content)
    # if input_match:
    #     input_data = input_match.group(1).strip()
    # else:
    #     input_data = ""
    # if output_match:
    #     output_data = output_match.group(1).strip()
    # else:
    #     output_data = ""
    # if exitcode_match:
    #     exitcode = exitcode_match.group(1).strip()
    # else:
    #     exitcode = ""
    content = file_path['source_code_base64']
    input_data = file_path['execute_input_base64']
    output_data = file_path['expected_output_basse64']
    verdict = file_path['compile_result']
    return content, input_data, output_data, verdict

def run_semantic(path, tag, q, user_id, url, testcase, attempt_id, submit_timestamp, connection):
    client = docker.from_env()
    content, input_data, output_data, verdict = extract_input_output_exitcode_verdict(testcase)
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
            create_a_testcase_result(connection, attempt_id, testcase, True, process.returncode, stderr, phase_id=1, submit_time=submit_timestamp,  user_id=user_id)
        else:
            statue = 'WA'
            create_a_testcase_result(connection, attempt_id, testcase, False, process.returncode, stderr, phase_id=1, submit_time=submit_timestamp,  user_id=user_id)
        q.put((0, statue))
        logging.info('[worker]run semantic check success {} statu:{} (request {}, url {})'.format(testcase, statue, user_id, url))
    except Exception as e:
        q.put((2, ''))
        logging.error('[worker]run semantic check failed {} error:{} (request {}, url {})'.format(testcase, e, user_id, url))
        create_a_testcase_result(connection, attempt_id, testcase, False, 0, phase_id=1, submit_time=submit_timestamp,  user_id=user_id)

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

def run_compile(connection, attempt_id, user_id, url, docker_id, submit_timestamp):
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
        update_submit_status(connection, attempt_id, 'status', 3)
        return False
    
    update_submit_status(connection, attempt_id, 'status', 2)
    # generate a new uuid for this compile
    compile_id = str(uuid.uuid4())
    # generate a new path for this compile and write to log
    compile_path = os.path.join(repo_build_root_path, compile_id)
    # create corresponding folder with exception check
    try:
        os.makedirs(compile_path)
    except Exception as e:
        logging.error('create compile path failed: {} (request {}, url {})'.format(compile_path, user_id, url))
        update_submit_status(connection, attempt_id, 'status', 3)
        return False
    logging.info('local compile path: {} (request {}, url {})'.format(compile_path, user_id, url))
    # change dir to compile_path
    os.chdir(compile_path)
    # clone the repo
    os.system('git clone {} compiler'.format(url))
    # check if the repo is cloned
    if not os.path.exists(os.path.join(compile_path, 'compiler')):
        logging.error('clone repo failed (request {}, url {})'.format(user_id, url))
        update_submit_status(connection, attempt_id, 'status', 3)
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
        update_submit_status(connection, attempt_id, 'status', 3)
        return False
    else:
        result = q.get()
        if result[0] != 0:
            logging.error('build docker image failed (request {}, url {})'.format(user_id, url))
            update_submit_status(connection, attempt_id, 'status', 3)
            return False
        else:
            logging.info('build docker image success (request {}, url {})'.format(user_id, url))
            # return True

    update_submit_status(connection, attempt_id, 'status', 4)
    # semantic_testcases = open(semantic_check_lib, 'r').readlines()
    semantic_ans = []
    semantic_testcases = fetch_testcase_by_phase(connection, 1)

    for testcase in semantic_testcases:
        p = multiprocessing.Process(target=run_semantic, args=(1, compile_path, user_id + ':latest', q, user_id, url, testcase, attempt_id, submit_timestamp, connection))
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
    
    # codegen_testcases = open(codegen_check_lib, 'r').readlines()
    # codegen_ans = []
    # ravel_user_testspace = os.path.join(ravel_bin_root, user_id)
    # if not os.path.exists(ravel_user_testspace):
    #     os.mkdir(ravel_user_testspace)
    # builtin_path = os.path.join(os.path.join(compile_path, 'compiler'), 'builtin.s')
    # if os.path.exists(builtin_path):
    #     shutil.copy(builtin_path, os.path.join(ravel_user_testspace, 'builtin.s'))

    # for testcase in codegen_testcases:
    #     test_path = os.path.join(codegen_check_root, testcase)

import redis
BASE_HOST = "" # TODO
r = redis.Redis(host=BASE_HOST, port=6379, decode_responses=True)
mysql_connection = create_mysql_connection(host_name="", user_name="", user_password="", db_name="compiler")

def get_stuid_image_repo(r):
    element = r.lpop() # pop from what?
    elements = element.split('|')
    stuid = elements[0]
    image_id = elements[1]
    repo_url = elements[2]
    return stuid, image_id, repo_url

def process():
    stuid, image_id, repo_url = get_stuid_image_repo(r)
    uid = select_uid_from_stuid(connection=mysql_connection, stuid=stuid)
    if uid is None:
        return False
    attempt_id, submit_timestamp = create_a_submit(connection=mysql_connection, uid=uid, stuid=stuid, git_repo=repo_url)
    run_compile(mysql_connection, attempt_id, uid, repo_url, image_id, submit_timestamp)