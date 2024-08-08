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
import redis
import mysql.connector
import base64


from SetupConstant import *
from MySQLHelper import *
from DockerHelper import *


def extract_input_output_exitcode_verdict(t):
    name = t[1]
    src_code = t[3]
    # decode src_code from base64
    src_code = base64.b64decode(src_code).decode('utf-8')
    verdict = t[4]
    return name, src_code, verdict


def extract_testcase_to_dict(t):
    return {
        'test_case_id': int(t[0]),
        'test_case_disp_name': t[1],
        'problem_phase': int(t[2]),
        'src_code': base64.b64decode(t[3]).decode('utf-8'),
        'verdict': int(t[4]),
        'error_canadiates': base64.b64decode(t[5]).decode('utf-8').split('|')
    }


def semantic_launcher(path, tag, user_id, url, testcase, attempt_id, submit_timestamp, connection, isExtend=False):
    client = docker.from_env()
    testcase = extract_testcase_to_dict(testcase)
    src_code = testcase['src_code']
    verdict = testcase['verdict']
    statue = ''
    
    try:
        process = subprocess.Popen(
            ['docker', 'run', '--rm', '-v', '{output_logs}:/oj_out' '-i', tag, '/bin/bash', 'launch_semantic.sh'],
            cwd=path,
            stdin=subprocess.PIPE,
            text=True,
            # shell=True
        )
        process.communicate(input=src_code)
        # add timeout 30s
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            create_a_testcase_result(connection, attempt_id, testcase, False, -1, '', '', -1, -1, 1 if not isExtend else 2, 30, submit_timestamp, user_id)
            logging.error('[worker]run semantic check timeout {} (request {}, url {})'.format(testcase, user_id, url))
            return
        stdout_data = open(os.path.join(output_logs, 'stdout.txt')).read().strip()[:1024]
        stderr_data = open(os.path.join(output_logs, 'stderr.txt')).read().strip()[:1024]
        stderr_data = base64.b64encode(stderr_data.encode('utf-8')).decode('utf-8')
        if (0 if process.returncode == 0 else 1) == verdict and not isExtend:
            statue = 'AC(1)'
            create_a_testcase_result(connection, attempt_id, testcase, True, process.returncode, stderr_data, '', -1, -1, 1 if not isExtend else 2, 30, submit_timestamp, user_id)
        elif (0 if process.returncode == 0 else 1) == verdict and isExtend:
            # verify output
            stdout_data = stdout_data.strip()
            if stdout_data == '' or stdout_data not in testcase['error_canadiates']:
                statue = 'WA(1)'
                create_a_testcase_result(connection, attempt_id, testcase, False, process.returncode, stderr_data, '', -1, -1, 1 if not isExtend else 2, 30, submit_timestamp, user_id)
            else:
                statue = 'AC(2)'
                create_a_testcase_result(connection, attempt_id, testcase, True, process.returncode, stderr_data, '', -1, -1, 1 if not isExtend else 2, 30, submit_timestamp, user_id)
        else:
            statue = 'WA(2)'
            create_a_testcase_result(connection, attempt_id, testcase, False, process.returncode, stderr_data, '', -1, -1, 1 if not isExtend else 2, 30, submit_timestamp, user_id)
        logging.info('[worker]run semantic check {} statu:{} (request {}, url {})'.format(testcase['test_case_disp_name'], statue, user_id, url))
    except Exception as e:
        logging.info('[worker]run semantic check {} with exception {} statu:{} (request {}, url {})'.format(testcase['test_case_disp_name'], e, statue, user_id, url))
        create_a_testcase_result(connection, attempt_id, testcase, False, process.returncode, stderr_data, '', -1, -1, 1 if not isExtend else 2, 30, submit_timestamp, user_id)



def run_semantic(connection, attempt_id, user_id, url, docker_id, submit_timestamp, compile_id, isExtend=False):
    # semantic_testcases = open(semantic_check_lib, 'r').readlines()
    if not isExtend:
        semantic_testcases = fetch_testcase_by_phase(connection, 1)
    else:
        semantic_testcases = fetch_testcase_by_phase(connection, 2)
    compile_path = os.path.join(repo_build_root_path, compile_id)

    overall_result = True
    for testcase in semantic_testcases:
        result = semantic_launcher(compile_path, docker_id, user_id, url, testcase, attempt_id, submit_timestamp, connection)
        overall_result = overall_result and result
        if not isExtend:
            logging.info('run semantic check for case {}, verdict {} (user {}, url {})'.format(testcase, result, user_id, url))
        else:
            logging.info('run semantic extend check for case {}, verdict {} (user {}, url {})'.format(testcase, result, user_id, url))
    return (overall_result, compile_id)