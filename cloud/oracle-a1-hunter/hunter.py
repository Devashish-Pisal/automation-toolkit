import os
import time
import requests
import oci


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


COMPARTMENT_ID = require_env("OCI_COMPARTMENT_ID")
SUBNET_ID = require_env("OCI_SUBNET_ID")
IMAGE_ID = require_env("OCI_IMAGE_ID")
SSH_PUBLIC_KEY = require_env("SSH_PUBLIC_KEY")
NTFY_TOPIC = require_env("NTFY_TOPIC")

A1_SHAPE = "VM.Standard.A1.Flex"

MAX_OCPUS = 2
MAX_MEMORY_GB = 12
MEMORY_STEP_GB = 2
MIN_MEMORY_GB_PER_OCPU = 1

SHAPES = [
    {"ocpus": ocpus, "memory": memory}
    for ocpus in range(MAX_OCPUS, 0, -1)
    for memory in range(MAX_MEMORY_GB, 0, -MEMORY_STEP_GB)
    if memory >= ocpus * MIN_MEMORY_GB_PER_OCPU
]

RUN_SECONDS = 240
STARTED = time.monotonic()

THROTTLE_RETRY = oci.retry.RetryStrategyBuilder(
    max_attempts=5,
    total_elapsed_time_seconds=60,
    service_error_retry_config={429: []},
    service_error_retry_on_any_5xx=False
).get_retry_strategy()


def send_ntfy(title, message):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "5",
                "Tags": "white_check_mark"
            },
            timeout=15
        )
    except Exception as e:
        print(f"Failed sending ntfy notification: {e}")


def get_clients():
    config = oci.config.from_file()
    compute = oci.core.ComputeClient(config)
    identity = oci.identity.IdentityClient(config)
    return config, compute, identity


def get_availability_domains(identity_client, tenancy_id):
    ads = identity_client.list_availability_domains(
        compartment_id=tenancy_id
    ).data

    return [ad.name for ad in ads]


def get_a1_instances(compute_client):
    instances = oci.pagination.list_call_get_all_results(
        compute_client.list_instances,
        compartment_id=COMPARTMENT_ID
    ).data

    return [
        instance for instance in instances
        if instance.shape == A1_SHAPE
        and instance.lifecycle_state != "TERMINATED"
    ]


def try_launch(compute_client, ad_name, ocpus, memory):
    launch_details = oci.core.models.LaunchInstanceDetails(
        compartment_id=COMPARTMENT_ID,
        availability_domain=ad_name,
        display_name=f"a1-{ocpus}cpu-{memory}gb",
        shape=A1_SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=ocpus,
            memory_in_gbs=memory
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=SUBNET_ID,
            assign_public_ip=True
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=IMAGE_ID
        ),
        metadata={"ssh_authorized_keys": SSH_PUBLIC_KEY}
    )

    return compute_client.launch_instance(
        launch_details,
        retry_strategy=THROTTLE_RETRY
    )


def try_resize(compute_client, instance_id, ocpus, memory):
    update_details = oci.core.models.UpdateInstanceDetails(
        display_name=f"a1-{ocpus}cpu-{memory}gb",
        shape_config=oci.core.models.UpdateInstanceShapeConfigDetails(
            ocpus=ocpus,
            memory_in_gbs=memory
        )
    )

    return compute_client.update_instance(
        instance_id,
        update_details,
        retry_strategy=THROTTLE_RETRY
    )


def out_of_time():
    if time.monotonic() - STARTED < RUN_SECONDS:
        return False

    print("Out of time. Will continue next run.")
    return True


def keep_trying(error, label):
    if error.status >= 500:
        print(f"Failed {label}: {error.message}")
        return True

    if error.status == 429:
        print("Rate limited by OCI. Will try again next run.")
        return False

    if error.code == "LimitExceeded":
        raise SystemExit(
            "Oracle says this size is over your A1 limit. Check the A1 limit "
            "on the Limits, Quotas and Usage page, set MAX_OCPUS and "
            "MAX_MEMORY_GB in hunter.py to match, and make sure no other A1 "
            "instance exists (check every compartment)."
        )

    raise error


def launch(compute, identity, tenancy_id):
    ads = get_availability_domains(identity, tenancy_id)

    print("Availability Domains:")
    for ad in ads:
        print(f"  {ad}")

    for shape in SHAPES:
        for ad in ads:
            ocpus = shape["ocpus"]
            memory = shape["memory"]

            if out_of_time():
                return

            print(
                f"Trying {ad} -> "
                f"{ocpus} OCPU / {memory} GB"
            )

            try:
                response = try_launch(
                    compute,
                    ad,
                    ocpus,
                    memory
                )
            except oci.exceptions.ServiceError as e:
                if keep_trying(e, f"{ad} {ocpus}/{memory}"):
                    continue
                return

            success_message = (
                f"OCI A1 instance created successfully!\n\n"
                f"AD: {ad}\n"
                f"OCPU: {ocpus}\n"
                f"RAM: {memory} GB\n"
                f"Instance ID:\n{response.data.id}"
            )

            print(success_message)

            send_ntfy(
                "OCI A1 Provisioned",
                success_message
            )

            return

    print("No capacity available this run.")


def upgrade(compute, instance):
    current_ocpus = instance.shape_config.ocpus
    current_memory = instance.shape_config.memory_in_gbs

    print(
        f"Existing A1 instance: {instance.display_name} "
        f"({current_ocpus:g} OCPU / {current_memory:g} GB, "
        f"{instance.lifecycle_state})"
    )

    if current_ocpus >= MAX_OCPUS and current_memory >= MAX_MEMORY_GB:
        print("Already at the maximum size. Nothing to do.")
        return

    if instance.lifecycle_state not in ("RUNNING", "STOPPED"):
        print("Instance is busy. Will try again next run.")
        return

    bigger_shapes = [
        shape for shape in SHAPES
        if shape["ocpus"] >= current_ocpus
        and shape["memory"] >= current_memory
        and (shape["ocpus"], shape["memory"]) != (current_ocpus, current_memory)
    ]

    for shape in bigger_shapes:
        ocpus = shape["ocpus"]
        memory = shape["memory"]

        if out_of_time():
            return

        print(f"Trying resize -> {ocpus} OCPU / {memory} GB")

        try:
            try_resize(compute, instance.id, ocpus, memory)
        except oci.exceptions.ServiceError as e:
            if keep_trying(e, f"resize {ocpus}/{memory}"):
                continue
            return

        success_message = (
            f"OCI A1 instance resized!\n\n"
            f"From: {current_ocpus:g} OCPU / {current_memory:g} GB\n"
            f"To: {ocpus} OCPU / {memory} GB\n"
            f"Instance ID:\n{instance.id}"
        )

        print(success_message)

        send_ntfy(
            "OCI A1 Resized",
            success_message
        )

        return

    print("No capacity to grow the instance this run.")


def main():
    config, compute, identity = get_clients()

    instances = get_a1_instances(compute)

    if len(instances) > 1:
        print("More than one A1 instance found. Not touching them.")
        return

    if instances:
        upgrade(compute, instances[0])
        return

    launch(compute, identity, config["tenancy"])


if __name__ == "__main__":
    main()
