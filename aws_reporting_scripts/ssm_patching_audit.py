#!/usr/bin/env python3
#
# Author: Dan Farmer
# License: GPL3
#   See the "LICENSE" file for full details

"""
List SSM Maintenance Windows, Tasks and Patching Baseline details.

- Maintenance Window schedule etc
- First returned Maintenance Window Task (further tasks are ignored)
  - Basic details
  - Target Patch Group
  - Task
  - Task operation
  - Patch Baseline
    - Patch Baseline name
    - Patch Baseline OS
    - Patch Filter (MSRC Severity)
    - Patch Filter (Classification)
    - Approval Delay
"""

import sys
import argparse
import csv
import boto3
import helpers

def main():
    """Gather and write CSV data, one row per Maintenance Window.

    Iterate through specified AWS regions
    Iterate through all SSM Maintenance Windows
    Query for first Maintenance Window Task
    Query for associated Task, Patch Group and Patch Baseline details
    """
    args = parse_args()
    if args.region == 'all' or args.region == 'ALL':
        region_list = list(helpers.get_region_list())
    else:
        # Check valid or return default region
        region_list = [helpers.get_region(args.region)]

    output = csv.writer(sys.stdout, delimiter=',', quotechar='"',
                        quoting=csv.QUOTE_ALL)

    # Header row
    output.writerow(['Account', 'Region', 'MW ID', 'MW Name', 'MW Schedule', 'MW TZ', 'Task 1 ID',
                     'Patch Group', 'Task', 'Operation', 'Baseline', 'Baseline Name', 'OS',
                     'Patch Filter (MSRC Sev)', 'Patch Filter (Class)', 'Approval Delay'])

    # Get AWS account number from STS
    account_number = boto3.client('sts').get_caller_identity()['Account']

    # Iterate through regions and Maintenance Windows
    for region in region_list:
        ssm_client = boto3.client('ssm', region_name=region)
        mw_enabled_filter = {'Key':'Enabled', 'Values':['true']}
        for maint_window in helpers.get_items(client=ssm_client,
                                              function='describe_maintenance_windows',
                                              item_name='WindowIdentities',
                                              Filters=[mw_enabled_filter]):
            # Gather data
            maint_window_info = get_maint_window_info(ssm_client, maint_window['WindowId'])
            task_1_id = get_maint_window_task_1(ssm_client, maint_window['WindowId'])
            task_info = get_task_info(ssm_client, maint_window['WindowId'], task_1_id)
            patch_tag = get_target_patch_tag(ssm_client, maint_window['WindowId'],
                                             task_info['target_id'])
            baseline_id = get_baseline_id(ssm_client, patch_tag)
            baseline_info = get_baseline_info(ssm_client, baseline_id)

            # Output data
            output.writerow([account_number,
                             region,
                             maint_window['WindowId'],
                             maint_window_info['name'],
                             maint_window_info['sched'],
                             maint_window_info['time_zone'],
                             task_1_id,
                             patch_tag,
                             task_info['task'],
                             task_info['operation'],
                             baseline_id,
                             baseline_info['name'],
                             baseline_info['operating_system'],
                             baseline_info['filter_msrc_sev'],
                             baseline_info['filter_class'],
                             baseline_info['delay']])

def parse_args():
    """Create arguments and populate variables from args.

    Return args namespace"""
    parser = argparse.ArgumentParser()
    parser.add_argument('-r', '--region', type=str, default=False,
                        help='AWS region; Use "all" for all regions')
    return parser.parse_args()

def get_maint_window_info(ssm_client, maint_window_id):
    """Return basic parameters of Maintenance Window."""
    name, sched, time_zone = '', '', ''
    maint_window = ssm_client.get_maintenance_window(WindowId=maint_window_id)
    name = maint_window['Name']
    sched = maint_window['Schedule']
    try:
        time_zone = maint_window['ScheduleTimezone']
    except KeyError:
        pass    # ScheduleTimezone is not set
    return {'name': name, 'sched': sched, 'time_zone': time_zone}

def get_maint_window_task_1(ssm_client, maint_window_id):
    """Return ID of first Maintenance Window Task in Maintenance Window."""
    task_1_id = ''
    task_list = ssm_client.describe_maintenance_window_tasks(
        WindowId=maint_window_id,
        MaxResults=10)
    try:
        task_1_id = task_list['Tasks'][0]['WindowTaskId']
    except IndexError:
        pass    # No Task exists for this Maintenance Window
    return task_1_id

def get_task_info(ssm_client, maint_window_id, task_id):
    """Return Target ID, Task and Operation of Maintenance Window Task."""
    target_id, task, operation = '', '', ''
    if task_id:
        maint_window_task = ssm_client.get_maintenance_window_task(
            WindowId=maint_window_id,
            WindowTaskId=task_id)

        target_id = next(item for item in maint_window_task['Targets']
                         if item['Key'] == 'WindowTargetIds')['Values'][0]
        task = maint_window_task['TaskArn']
        try:
            operation = (maint_window_task['TaskInvocationParameters']
                         ['RunCommand']['Parameters']['Operation'][0])
        except (KeyError, IndexError):
            pass    # No 'Operation' parameter or values set for task
    return {'target_id': target_id, 'task': task, 'operation': operation}

def get_target_patch_tag(ssm_client, maint_window_id, target_id):
    """Return 'Patch Group' tag value of Maintenance Window Target."""
    patch_tag = ''
    filters = {'Key':'WindowTargetId', 'Values':[target_id]}
    if target_id:
        try:
            target_list = ssm_client.describe_maintenance_window_targets(
                WindowId=maint_window_id,
                Filters=[filters])
        except KeyError:
            pass    # No targets
        try:
            patch_tag = next(item for item in target_list['Targets'][0]['Targets']
                             if item['Key'] == 'tag:Patch Group')['Values'][0]
        except (KeyError, IndexError):
            pass    # No 'Patch Group' tag
    return patch_tag

def get_baseline_id(ssm_client, patch_tag):
    """Return ID of Patch Baseline for Patch Group."""
    baseline_id = ''
    if patch_tag:
        baseline = ssm_client.get_patch_baseline_for_patch_group(PatchGroup=patch_tag)
        baseline_id = baseline['BaselineId']
    return baseline_id

def get_baseline_info(ssm_client, baseline_id):
    """Return Patch Baseline properties."""
    name, operating_system, filter_msrc_sev, filter_class, delay = '', '', '', '', ''
    if baseline_id:
        baseline = ssm_client.get_patch_baseline(BaselineId=baseline_id)
        name = baseline['Name']
        operating_system = baseline['OperatingSystem']
        patch_filters = (baseline['ApprovalRules']['PatchRules']
                         [0]['PatchFilterGroup']['PatchFilters'])
        filter_msrc_sev = ",".join(next(item for item in patch_filters
                                        if item['Key'] == 'MSRC_SEVERITY')['Values'])
        filter_class = ",".join(next(item for item in patch_filters
                                     if item['Key'] == 'CLASSIFICATION')['Values'])
        delay = baseline['ApprovalRules']['PatchRules'][0]['ApproveAfterDays']
    return {'name': name,
            'operating_system': operating_system,
            'filter_msrc_sev': filter_msrc_sev,     # Patch filter (MSRC severity)
            'filter_class': filter_class,           # Patch filter (classification)
            'delay': delay}

if __name__ == '__main__':
    main()
