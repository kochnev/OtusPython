# %load log_analyzer.py
# !/usr/bin/env python3

import argparse
import gzip
import json
import logging
import os
import re
import statistics
import time
from collections import namedtuple
from datetime import datetime
from json import JSONDecodeError
from string import Template
from typing import Union

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
LogInformation = namedtuple('LogInformation', 'date path')


def prepare_report(params: dict, path_to_template):
    with open(path_to_template, "r") as f:
        text_tempalte = f.read()
    page = Template(text_tempalte)
    page = page.safe_substitute(params)
    return page


def read_log(path_to_log):
    is_gz_file = path_to_log.endswith(".gz")
    if is_gz_file:
        log = gzip.open(path_to_log)
    else:
        log = open(path_to_log)

    for row in log:
        if is_gz_file:  # gz return binary row
            row = row.decode('utf-8')
        yield row


def analyze_log(path_to_log: str, report_size: int):
    url_pattern = re.compile('[A-Z]+\s(?P<url>\S+)\sHTTP/\d\.\d.* (?P<t_execution>\d+(\.\d+)?)')
    requests = []
    total_time = 0
    total_req = 0
    stat = {}
    log = read_log(path_to_log)
    for row in log:
        matches = url_pattern.search(row)
        if matches:
            url = matches.group('url')
            # row in logfile has mark about time of execution.
            t_execution = float(matches.group('t_execution'))
            stat.setdefault(url, []).append(t_execution)
            total_time += t_execution
            total_req += 1
        else:
            logging.info('Row {} is missed'.format(row.strip()))

    for url, t_executions in stat.items():
        metrics = {}
        avg_time = round(statistics.mean(t_executions), 3)
        total_req_for_req = len(t_executions)
        total_time_for_req = round(sum(t_executions), 3)
        metrics['time_avg'] = avg_time
        metrics['count'] = total_req_for_req
        metrics['time_sum'] = total_time_for_req
        metrics['time_max'] = max(t_executions)
        metrics['url'] = url
        metrics['time_med'] = round(statistics.median(t_executions), 3)
        metrics['time_perc'] = round(total_time_for_req / total_time * 100, 3)
        metrics['count_perc'] = round(total_req_for_req / total_req * 100, 3)
        requests.append(metrics)

    ordered_requests = sorted(requests, key=lambda request: request['time_sum'], reverse=True)
    most_slow_requests = ordered_requests[:report_size]
    return most_slow_requests


def configure_logger(config: dict):
    # Configure our logger
    path_to_log_file = config.get('LOG_FILE')
    if path_to_log_file:
        # Write to file
        handler = logging.FileHandler(path_to_log_file)
    else:
        # Write to stdout
        handler = logging.StreamHandler()

    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname).1s %(message)s',
                        datefmt='%Y.%m.%d %H:%M:%S', handlers=[handler])


def params_unification(config):
    """Convert path to file"""
    current_dir = os.getcwd()
    for key, value in config.items():
        if isinstance(value, str) and value.startswith('./'):  # We've relative path.
            # transform relative to absolute
            config[key] = current_dir + value[1:]
    return config


def get_path_to_last_nginx_log(path_to_logs) -> Union[None, LogInformation]:
    """Return newest log-file in dir
    log_format ui_short '$remote_addr $remote_user $http_x_real_ip [$time_local] "$request" '
                        '$status $body_bytes_sent "$http_referer" '
                        '"$http_user_agent" "$http_x_forwarded_for" "$http_X_REQUEST_ID" "$http_X_RB_USER" '
                        '$request_time';

    """
    if not os.path.exists(path_to_logs):
        logging.error("Sorry, directory {} wasn't found".format(path_to_logs))
        return None

    file_mask = re.compile('^nginx-access-ui.log-(?P<day_of_log>\d{8})(\.gz)?$')
    # Try to find last log-file in DIR
    logs = os.listdir(path_to_logs)
    valid_logs = []
    for log in logs:
        match = file_mask.search(log)
        if match:  # We find valid log
            day_of_log = datetime.strptime(match.group('day_of_log'), '%Y%m%d').date()
            log_file = '{}/{}'.format(path_to_logs, match.string)
            log_info = LogInformation(day_of_log, log_file)
            valid_logs.append(log_info)

    if valid_logs:
        # We know that log have date difference, but find newest
        valid_log = max(valid_logs, key=lambda log: log.date)
        return valid_log
    else:
        logging.info("Not found logs in directory {}".format(path_to_logs))
        return None


def refresh_ts(path_to_ts_file: str):
    """Update timestamp last report creation"""
    ts = int(time.time())
    with open(path_to_ts_file, "w") as f:
        f.write(str(ts))
    os.utime(path_to_ts_file, (ts, ts))
    logging.info("TS-file was updated")


def build_config(options):
    def parse_config(path):
        with open(path, 'r') as f:
            return json.load(f)

    current_dir = os.path.dirname(os.path.realpath(__file__))
    config = parse_config('{}/local_conf.conf'.format(current_dir))

    path_to_exteranal_config = options.config
    if path_to_exteranal_config:
        try:
            external_config = parse_config(path_to_exteranal_config)
            config.update(external_config)
        except JSONDecodeError:
            logging.error("Please, check your config")
            raise Exception("Please, check your config")
    return params_unification(config)


def parse_log(path_to_last_log, report_size):
    logging.info("Nginx log analyze was started")
    slow_requests = analyze_log(path_to_last_log, report_size)
    logging.info("Nginx log analyze was finished")
    return slow_requests


def make_report(path_to_new_daily_report: str, path_to_template, slow_requests):
    logging.info("Make daily report from template")
    page = prepare_report({'table_json': slow_requests}, path_to_template)

    with open(path_to_new_daily_report, 'w') as f:
        f.write(page)
    logging.info("Daily report was wrote")
    return page


def main(config: dict):
    logging.info("Log analyzer run")
    about_last_log = get_path_to_last_nginx_log(config["LOG_DIR"])
    if about_last_log:
        day_of_report = datetime.strftime(about_last_log.date, '%Y.%m.%d')
        path_to_daily_report = '{0}/report-{1}.html'.format(config["REPORT_DIR"], day_of_report)
        if not os.path.exists(path_to_daily_report):
            report_size = config['REPORT_SIZE']
            slow_requests = parse_log(about_last_log.path, report_size)
            make_report(path_to_daily_report, config['REPORT_TEMPLATE'], slow_requests)
            # Report
            refresh_ts(config['TS_FILE'])

    logging.info("Log analyzer stopped")


if __name__ == "__main__":
    m = argparse.ArgumentParser(description="Log analyzer", prog="log_analyzer")
    m.add_argument("--config", "-c", type=str, default='', help="Program config")
    options = m.parse_args()
    config = build_config(options)
    configure_logger(config)
    try:
        main(config)
    except Exception as e:
        # If something going wrong
        logging.exception(e, exc_info=True)
