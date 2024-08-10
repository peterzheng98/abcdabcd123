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

from SetupConstant import *


# set logging with date time, and both write to files
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=log_path,
                    filemode='a')


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
        logging.info('fetch user_id for stu_id {} successfully: result {}'.format(stuid, result))
        return result[0]
    else:
        logging.error(f"error in fetch user_id for stu_id {stuid}")
        return None

def create_a_submit(connection, uid, stuid, git_repo):
    insert_submit_query = """
INSERT INTO compiler.JudgeAttempts (
    user_id, problem_id, submission_time, status, githash, git_message, attempt_hash, stuid
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
    submit_timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    data = (uid, 0, submit_timestamp, 1, 'None', 'Under Cloning', 'None', stuid)
    return execute_query(connection, insert_submit_query, data, info="create a new submit"), submit_timestamp

def update_submit_status(connection, attempt_id, item, value):
    update_query = f"UPDATE compiler.JudgeAttempts SET '{item}' = %s WHERE attempt_id = %s"
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
