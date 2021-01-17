# -*- encoding: utf8 -*-
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2017 Marek Marczykowski-Górecki
#                               <marmarek@invisiblethingslab.com>
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, see <https://www.gnu.org/licenses/>.

### Rework to enable asking for clipboard paste ~ Matt 2021-01-17

import argparse
import logging
import logging.handlers
import os

import sys

import qubespolicy

parser = argparse.ArgumentParser(description="Evaluate qrexec policy")

parser.add_argument("--assume-yes-for-ask", action="store_true",
    dest="assume_yes_for_ask", default=False,
    help="Allow run of service without confirmation if policy say 'ask'")
parser.add_argument("--just-evaluate", action="store_true",
    dest="just_evaluate", default=False,
    help="Do not run the service, only evaluate policy; "
         "retcode=0 means 'allow'")
parser.add_argument('domain_id', metavar='src-domain-id',
    help='Source domain ID (Xen ID or similar, not Qubes ID)')
parser.add_argument('domain', metavar='src-domain-name',
    help='Source domain name')
parser.add_argument('target', metavar='dst-domain-name',
    help='Target domain name')
parser.add_argument('service_name', metavar='service-name',
    help='Service name')
parser.add_argument('process_ident', metavar='process-ident',
    help='Qrexec process identifier - for connecting data channel')


def create_default_policy(service_name):
    policy_file = os.path.join(qubespolicy.POLICY_DIR, service_name)
    with open(policy_file, "w") as policy:
        policy.write(
            "## Policy file automatically created on first service call.\n")
        policy.write(
            "## Feel free to edit.\n")
        policy.write("## Note that policy parsing stops at the first match\n")
        policy.write("\n")
        policy.write("## Please use a single # to start your custom comments\n")
        policy.write("\n")
        policy.write("@anyvm  @anyvm  ask\n")


def main(args=None):
    args = parser.parse_args(args)

    # Add source domain information, required by qrexec-client for establishing
    # connection
    caller_ident = args.process_ident + "," + args.domain + "," + args.domain_id
    log = logging.getLogger('qubespolicy')
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = logging.handlers.SysLogHandler(address='/dev/log')
        log.addHandler(handler)
    log_prefix = 'qrexec: {}: {} -> {}:'.format(
        args.service_name, args.domain, args.target)
    try:
        system_info = qubespolicy.get_system_info()
    except qubespolicy.QubesMgmtException as e:
        log.error('%s error getting system info: %s', log_prefix, str(e))
        return 1
    try:
        try:
            policy = qubespolicy.Policy(args.service_name)
        except qubespolicy.PolicyNotFound:
            service_name = args.service_name.split('+')[0]
            import pydbus
            bus = pydbus.SystemBus()
            proxy = bus.get('org.qubesos.PolicyAgent',
                '/org/qubesos/PolicyAgent')
            create_policy = proxy.ConfirmPolicyCreate(
                args.domain, service_name)
            if create_policy:
                create_default_policy(service_name)
                policy = qubespolicy.Policy(args.service_name)
            else:
                raise

        action = policy.evaluate(system_info, args.domain, args.target)

        # MATT: Unconditionally ignore --assume-yes-for-ask.
        # --assume-yes-for-ask will be re-enabled once qubes-guid supports a
        # /etc/qubes/guid.conf option to not pass it.
        #if args.assume_yes_for_ask and action.action == qubespolicy.Action.ask:
        #    action.action = qubespolicy.Action.allow

        # MATT: Enable asking even if just_evaluate is used.
        #if args.just_evaluate:
        #    return {
        #        qubespolicy.Action.allow: 0,
        #        qubespolicy.Action.deny: 1,
        #        qubespolicy.Action.ask: 1,
        #    }[action.action]

        if action.action == qubespolicy.Action.ask:
            # late import to save on time for allow/deny actions
            import pydbus
            bus = pydbus.SystemBus()
            proxy = bus.get('org.qubesos.PolicyAgent',
                '/org/qubesos/PolicyAgent')

            icons = {name: system_info['domains'][name]['icon']
                for name in system_info['domains'].keys()}
            for dispvm_base in system_info['domains']:
                if not (system_info['domains'][dispvm_base]
                        ['template_for_dispvms']):
                    continue
                dispvm_api_name = '@dispvm:' + dispvm_base
                icons[dispvm_api_name] = \
                    system_info['domains'][dispvm_base]['icon']
                icons[dispvm_api_name] = \
                    icons[dispvm_api_name].replace('app', 'disp')

            # MATT: If just_evaluate, then the caller isn't willing to change
            # the target, so we should force that target.
            if args.just_evaluate:
                target = action.original_target
                targets_for_ask = [target]
            else:
                target = action.target
                targets_for_ask = action.targets_for_ask

            response = proxy.Ask(args.domain, args.service_name,
                targets_for_ask, target or '', icons)
            if response:
                action.handle_user_response(True, response)
            else:
                action.handle_user_response(False)
        log.info('%s allowed to %s', log_prefix, str(action.target))
        # MATT: just_evaluate mode now exits here.
        if args.just_evaluate:
            return 0
        action.execute(caller_ident)
    except qubespolicy.PolicySyntaxError as e:
        log.error('%s error loading policy: %s', log_prefix, str(e))
        return 1
    except qubespolicy.AccessDenied as e:
        log.info('%s denied: %s', log_prefix, str(e))
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
