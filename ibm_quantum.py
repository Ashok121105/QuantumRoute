"""Secure IBM Quantum service initialization; no jobs are submitted here."""

from dataclasses import dataclass, field
from enum import Enum
import importlib
import os
from pathlib import Path

from dotenv import load_dotenv


API_KEY_ENV_VAR = "IBM_QUANTUM_API_KEY"
_PROJECT_ENV_FILE = Path(__file__).resolve().with_name(".env")


class IBMQuantumStatus(str, Enum):
    SERVICE_CONNECTED = "service_connected"
    NO_CREDENTIALS = "no_credentials"
    INVALID_CREDENTIALS = "invalid_credentials"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    SERVICE_ERROR = "service_error"
    NO_ACCESSIBLE_BACKENDS = "no_accessible_backends"
    BACKEND_DISCOVERY_SUCCESSFUL = "backend_discovery_successful"


@dataclass(frozen=True)
class IBMQuantumConnection:
    """Safe connection status with an optional, non-repr service handle."""

    connected: bool
    message: str
    status: IBMQuantumStatus
    service: object | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class IBMBackendMetadata:
    """Non-sensitive information useful for reviewing available backends."""

    name: str
    num_qubits: int | None
    operational: bool | None
    status_message: str | None
    pending_jobs: int | None
    simulator: bool | None
    supported_operations: tuple[str, ...] | None = None


@dataclass(frozen=True)
class IBMBackendDiscovery:
    """Read-only discovery outcome with no job or backend handles."""

    connected: bool
    status: IBMQuantumStatus
    message: str
    backends: tuple[IBMBackendMetadata, ...] = ()


def connect_ibm_quantum() -> IBMQuantumConnection:
    """Initialize IBM Quantum Runtime without submitting a quantum job."""
    load_dotenv(dotenv_path=_PROJECT_ENV_FILE, override=False)
    api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if not api_key:
        return IBMQuantumConnection(
            connected=False,
            message=(
                f"Missing {API_KEY_ENV_VAR}. Set it in the environment or local .env file."
            ),
            status=IBMQuantumStatus.NO_CREDENTIALS,
        )

    try:
        runtime = importlib.import_module("qiskit_ibm_runtime")
        service_type = runtime.QiskitRuntimeService
    except (ImportError, AttributeError):
        return IBMQuantumConnection(
            connected=False,
            message=(
                "qiskit-ibm-runtime is unavailable. Install the project dependencies "
                "from requirements.txt."
            ),
            status=IBMQuantumStatus.RUNTIME_UNAVAILABLE,
        )

    try:
        service = service_type(
            channel="ibm_quantum_platform",
            token=api_key,
        )
    except Exception as error:
        status = (
            IBMQuantumStatus.INVALID_CREDENTIALS
            if _is_authentication_error(error)
            else IBMQuantumStatus.SERVICE_ERROR
        )
        return IBMQuantumConnection(
            connected=False,
            message=(
                "IBM Quantum credentials were rejected."
                if status is IBMQuantumStatus.INVALID_CREDENTIALS
                else (
                    "IBM Quantum connection failed. Check the configured credentials and "
                    "service availability."
                )
            ),
            status=status,
        )

    return IBMQuantumConnection(
        connected=True,
        message="IBM Quantum service initialized; no quantum job was submitted.",
        status=IBMQuantumStatus.SERVICE_CONNECTED,
        service=service,
    )


def discover_ibm_backends(
    connection: IBMQuantumConnection | None = None,
) -> IBMBackendDiscovery:
    """List account-visible backends and safe metadata without submitting jobs."""
    connection = connection or connect_ibm_quantum()
    if not connection.connected or connection.service is None:
        return IBMBackendDiscovery(
            connected=False,
            status=connection.status,
            message=connection.message,
        )

    try:
        backend_objects = tuple(connection.service.backends())
    except Exception as error:
        status = (
            IBMQuantumStatus.INVALID_CREDENTIALS
            if _is_authentication_error(error)
            else IBMQuantumStatus.SERVICE_ERROR
        )
        message = (
            "IBM Quantum credentials were rejected during backend discovery."
            if status is IBMQuantumStatus.INVALID_CREDENTIALS
            else "IBM Quantum backend discovery failed due to a service error."
        )
        return IBMBackendDiscovery(
            connected=True,
            status=status,
            message=message,
        )

    if not backend_objects:
        return IBMBackendDiscovery(
            connected=True,
            status=IBMQuantumStatus.NO_ACCESSIBLE_BACKENDS,
            message="IBM Quantum service connected successfully; no accessible backends were found.",
        )

    backends = tuple(_backend_metadata(backend) for backend in backend_objects)
    return IBMBackendDiscovery(
        connected=True,
        status=IBMQuantumStatus.BACKEND_DISCOVERY_SUCCESSFUL,
        message=f"IBM Quantum backend discovery successful: {len(backends)} backend(s) found.",
        backends=backends,
    )


def ibm_quantum_status_report(
    result: IBMQuantumConnection | IBMBackendDiscovery,
) -> str:
    """Format a safe status summary without exposing credentials or exceptions."""
    if result.status is IBMQuantumStatus.SERVICE_CONNECTED:
        return "IBM Quantum service connection successful; backend discovery has not run."
    if result.status is IBMQuantumStatus.BACKEND_DISCOVERY_SUCCESSFUL:
        return f"IBM Quantum service connection successful; {result.message}"
    if result.status is IBMQuantumStatus.NO_ACCESSIBLE_BACKENDS:
        return result.message
    if result.status is IBMQuantumStatus.NO_CREDENTIALS:
        return "No IBM Quantum credentials are configured."
    if result.status is IBMQuantumStatus.INVALID_CREDENTIALS:
        return "IBM Quantum credentials are invalid or were rejected."
    if result.status is IBMQuantumStatus.RUNTIME_UNAVAILABLE:
        return "IBM Quantum Runtime is unavailable; install project dependencies."
    return "IBM Quantum service or backend discovery is unavailable due to a service error."


def _backend_metadata(backend: object) -> IBMBackendMetadata:
    name = str(getattr(backend, "name", "Unknown backend"))
    raw_qubits = getattr(backend, "num_qubits", None)
    num_qubits = raw_qubits if isinstance(raw_qubits, int) else None

    try:
        status = backend.status()
    except Exception:
        status = None

    operational = getattr(status, "operational", None)
    status_message = getattr(status, "status_msg", None)
    pending_jobs = getattr(status, "pending_jobs", None)

    simulator = getattr(backend, "simulator", None)
    if simulator is None:
        try:
            simulator = getattr(backend.configuration(), "simulator", None)
        except Exception:
            simulator = None

    raw_operations = getattr(backend, "operation_names", None)
    supported_operations = (
        tuple(str(operation) for operation in raw_operations)
        if isinstance(raw_operations, (list, tuple, set))
        else None
    )

    return IBMBackendMetadata(
        name=name,
        num_qubits=num_qubits,
        operational=operational if isinstance(operational, bool) else None,
        status_message=(str(status_message) if status_message is not None else None),
        pending_jobs=(pending_jobs if isinstance(pending_jobs, int) else None),
        simulator=simulator if isinstance(simulator, bool) else None,
        supported_operations=supported_operations,
    )


def _is_authentication_error(error: Exception) -> bool:
    error_name = type(error).__name__.casefold()
    authentication_error_names = {
        "ibmnotauthorizederror",
        "ibmauthenticationerror",
        "authenticationerror",
        "unauthorizederror",
        "invalidtokenerror",
    }
    return error_name in authentication_error_names or getattr(
        error, "status_code", None
    ) in (401, 403)