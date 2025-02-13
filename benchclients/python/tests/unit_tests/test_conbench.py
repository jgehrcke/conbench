# Copyright (c) 2022, Voltron Data.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest
import requests
from _pytest.logging import LogCaptureFixture

from benchclients import ConbenchClient

from .mocks import MockAdapter


class TestConbenchClient:
    @property
    def cb(self):
        return ConbenchClient(adapter=MockAdapter())

    def test_conbench_fails_missing_env(self, missing_conbench_env):
        with pytest.raises(ValueError, match="CONBENCH_URL"):
            self.cb

    @pytest.mark.parametrize("path", ["/error_with_content", "/error_without_content"])
    def test_client_error_handling(self, conbench_env, path, caplog: LogCaptureFixture):
        with pytest.raises(requests.HTTPError, match="404"):
            self.cb.get(path)

        if path == "/error_with_content":
            assert 'Response content: {"code":' in caplog.text
        else:
            assert "Response content: None" in caplog.text
