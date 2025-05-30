import os
import sys
import traceback
from unittest import mock
import pytest

from dotenv import load_dotenv
from moto import mock_aws
import boto3

import litellm.proxy
import litellm.proxy.proxy_server

load_dotenv()
import io
import os

# this file is to test litellm/proxy

sys.path.insert(
    0, os.path.abspath("../..")
)  # Adds the parent directory to the system path
import asyncio
import logging

from litellm.proxy.proxy_server import ProxyConfig

INVALID_FILES = ["config_with_missing_include.yaml"]


@pytest.mark.asyncio
async def test_basic_reading_configs_from_files():
    """
    Test that the config is read correctly from the files in the example_config_yaml folder
    """
    proxy_config_instance = ProxyConfig()
    current_path = os.path.dirname(os.path.abspath(__file__))
    example_config_yaml_path = os.path.join(current_path, "example_config_yaml")

    # get all the files from example_config_yaml
    files = os.listdir(example_config_yaml_path)
    print(files)

    for file in files:
        if file in INVALID_FILES:  # these are intentionally invalid files
            continue
        print("reading file=", file)
        config_path = os.path.join(example_config_yaml_path, file)
        config = await proxy_config_instance.get_config(config_file_path=config_path)
        print(config)


@pytest.mark.asyncio
async def test_read_config_from_bad_file_path():
    """
    Raise an exception if the file path is not valid
    """
    proxy_config_instance = ProxyConfig()
    config_path = "non-existent-file.yaml"
    with pytest.raises(Exception):
        config = await proxy_config_instance.get_config(config_file_path=config_path)


@pytest.mark.asyncio
async def test_read_config_file_with_os_environ_vars():
    """
    Ensures os.environ variables are read correctly from config.yaml
    Following vars are set as os.environ variables in the config.yaml file
    - DEFAULT_USER_ROLE
    - AWS_ACCESS_KEY_ID
    - AWS_SECRET_ACCESS_KEY
    - AZURE_GPT_4O
    - FIREWORKS
    """

    _env_vars_for_testing = {
        "DEFAULT_USER_ROLE": "admin",
        "AWS_ACCESS_KEY_ID": "1234567890",
        "AWS_SECRET_ACCESS_KEY": "1234567890",
        "AZURE_GPT_4O": "1234567890",
        "FIREWORKS": "1234567890",
    }

    _old_env_vars = {}
    for key, value in _env_vars_for_testing.items():
        if key in os.environ:
            _old_env_vars[key] = os.environ.get(key)
        os.environ[key] = value

    # Read config
    proxy_config_instance = ProxyConfig()
    current_path = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(
        current_path, "example_config_yaml", "config_with_env_vars.yaml"
    )
    config = await proxy_config_instance.get_config(config_file_path=config_path)
    print(config)

    # Add assertions
    assert (
        config["litellm_settings"]["default_internal_user_params"]["user_role"]
        == "admin"
    )
    assert (
        config["litellm_settings"]["s3_callback_params"]["s3_aws_access_key_id"]
        == "1234567890"
    )
    assert (
        config["litellm_settings"]["s3_callback_params"]["s3_aws_secret_access_key"]
        == "1234567890"
    )

    for model in config["model_list"]:
        if "azure" in model["litellm_params"]["model"]:
            assert model["litellm_params"]["api_key"] == "1234567890"
        elif "fireworks" in model["litellm_params"]["model"]:
            assert model["litellm_params"]["api_key"] == "1234567890"

    # cleanup
    for key, value in _env_vars_for_testing.items():
        if key in _old_env_vars:
            os.environ[key] = _old_env_vars[key]
        else:
            del os.environ[key]


@pytest.mark.asyncio
async def test_basic_include_directive():
    """
    Test that the include directive correctly loads and merges configs
    """
    proxy_config_instance = ProxyConfig()
    current_path = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(
        current_path, "example_config_yaml", "config_with_include.yaml"
    )

    config = await proxy_config_instance.get_config(config_file_path=config_path)

    # Verify the included model list was merged
    assert len(config["model_list"]) > 0
    assert any(
        model["model_name"] == "included-model" for model in config["model_list"]
    )

    # Verify original config settings remain
    assert config["litellm_settings"]["callbacks"] == ["prometheus"]


@pytest.mark.asyncio
async def test_missing_include_file():
    """
    Test that a missing included file raises FileNotFoundError
    """
    proxy_config_instance = ProxyConfig()
    current_path = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(
        current_path, "example_config_yaml", "config_with_missing_include.yaml"
    )

    with pytest.raises(FileNotFoundError):
        await proxy_config_instance.get_config(config_file_path=config_path)


@pytest.mark.asyncio
async def test_multiple_includes():
    """
    Test that multiple files in the include list are all processed correctly
    """
    proxy_config_instance = ProxyConfig()
    current_path = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(
        current_path, "example_config_yaml", "config_with_multiple_includes.yaml"
    )

    config = await proxy_config_instance.get_config(config_file_path=config_path)

    # Verify models from both included files are present
    assert len(config["model_list"]) == 2
    assert any(
        model["model_name"] == "included-model-1" for model in config["model_list"]
    )
    assert any(
        model["model_name"] == "included-model-2" for model in config["model_list"]
    )

    # Verify original config settings remain
    assert config["litellm_settings"]["callbacks"] == ["prometheus"]


def test_add_callbacks_from_db_config():
    """Test that callbacks are added correctly and duplicates are prevented"""
    # Setup
    from litellm.integrations.langfuse.langfuse_prompt_management import (
        LangfusePromptManagement,
    )

    proxy_config = ProxyConfig()

    # Reset litellm callbacks before test
    litellm.success_callback = []
    litellm.failure_callback = []

    # Test Case 1: Add new callbacks
    config_data = {
        "litellm_settings": {
            "success_callback": ["langfuse", "custom_callback_api"],
            "failure_callback": ["langfuse"],
        }
    }

    proxy_config._add_callbacks_from_db_config(config_data)

    # 1 instance of LangfusePromptManagement should exist in litellm.success_callback
    num_langfuse_instances = sum(
        isinstance(callback, LangfusePromptManagement)
        for callback in litellm.success_callback
    )
    assert num_langfuse_instances == 1
    assert len(litellm.success_callback) == 2
    assert len(litellm.failure_callback) == 1

    # Test Case 2: Try adding duplicate callbacks
    proxy_config._add_callbacks_from_db_config(config_data)

    # Verify no duplicates were added
    assert len(litellm.success_callback) == 2
    assert len(litellm.failure_callback) == 1

    # Cleanup
    litellm.success_callback = []
    litellm.failure_callback = []
    litellm._known_custom_logger_compatible_callbacks = []


def test_add_callbacks_invalid_input():
    """Test handling of invalid input for callbacks"""
    proxy_config = ProxyConfig()

    # Reset callbacks
    litellm.success_callback = []
    litellm.failure_callback = []

    # Test Case 1: Invalid callback format
    config_data = {
        "litellm_settings": {
            "success_callback": "invalid_string_format",  # Should be a list
            "failure_callback": 123,  # Should be a list
        }
    }

    proxy_config._add_callbacks_from_db_config(config_data)

    # Verify no callbacks were added with invalid input
    assert len(litellm.success_callback) == 0
    assert len(litellm.failure_callback) == 0

    # Test Case 2: Missing litellm_settings
    config_data = {}
    proxy_config._add_callbacks_from_db_config(config_data)

    # Verify no callbacks were added
    assert len(litellm.success_callback) == 0
    assert len(litellm.failure_callback) == 0

    # Cleanup
    litellm.success_callback = []
    litellm.failure_callback = []


@pytest.mark.asyncio
async def test_reading_configs_with_includes_from_s3():
    """
    Test that the config is read correctly from the S3 and read includes
    """
    BUCKET_NAME = "config-bucket"
    config_files = ("config_with_multiple_includes.yaml", "models_file_1.yaml", "models_file_2.yaml")
    os.environ["LITELLM_CONFIG_BUCKET_NAME"] = BUCKET_NAME
    os.environ["LITELLM_CONFIG_BUCKET_OBJECT_KEY"] = config_files[0]
    os.environ["LITELLM_CONFIG_BUCKET_TYPE"] = "S3"
    with mock_aws():
        # setup s3 bucket and put config files
        current_path = os.path.dirname(os.path.abspath(__file__))
        s3_client = boto3.client("s3")
        s3_client.create_bucket(Bucket=BUCKET_NAME)
        for file_name in config_files:
            config_path = os.path.join(
                current_path, "example_config_yaml", file_name,
            )
            s3_client.put_object(
                Bucket="config-bucket", Key=file_name, Body=open(config_path, 'rb')
            )
        proxy_config_instance = ProxyConfig()    
        config = await proxy_config_instance.get_config()

        assert config == {
            'model_list': [
                {'model_name': 'included-model-1', 'litellm_params': {'model': 'gpt-4'}},
                {'model_name': 'included-model-2', 'litellm_params': {'model': 'gpt-3.5-turbo'}}
            ],
            'litellm_settings': {'callbacks': ['prometheus']},
        }

    # unset the env variable to avoid side-effects on other tests
    del os.environ["LITELLM_CONFIG_BUCKET_NAME"]
    del os.environ["LITELLM_CONFIG_BUCKET_OBJECT_KEY"]
    del os.environ["LITELLM_CONFIG_BUCKET_TYPE"]


@pytest.mark.asyncio
async def test_reading_configs_from_s3_file_not_found():
    """
    Test that the config is not present S3
    """
    BUCKET_NAME = "config-bucket"
    config_files = ("config_with_multiple_includes.yaml", "models_file_1.yaml", "models_file_2.yaml")
    os.environ["LITELLM_CONFIG_BUCKET_NAME"] = BUCKET_NAME
    os.environ["LITELLM_CONFIG_BUCKET_OBJECT_KEY"] = config_files[0]
    os.environ["LITELLM_CONFIG_BUCKET_TYPE"] = "S3"
    with mock_aws():
        # setup s3 bucket but do not put file
        s3_client = boto3.client("s3")
        s3_client.create_bucket(Bucket=BUCKET_NAME)
        proxy_config_instance = ProxyConfig()
        with pytest.raises(Exception) as ex:
            await proxy_config_instance.get_config()
        
        assert str(ex.value) == "Unable to load config from given source."

    # unset the env variable to avoid side-effects on other tests
    del os.environ["LITELLM_CONFIG_BUCKET_NAME"]
    del os.environ["LITELLM_CONFIG_BUCKET_OBJECT_KEY"]
    del os.environ["LITELLM_CONFIG_BUCKET_TYPE"]