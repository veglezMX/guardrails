"""Prepare private local CA material; keys never enter chart values or evidence."""

import ipaddress
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def private_directory_tree(root: Path, target: Path):
    """Create every component inside the private deployment boundary explicitly."""
    root, target = root.absolute(), target.absolute()
    if ".." in target.parts or not target.is_relative_to(root):
        raise ValueError("certificate_directory_outside_private_root")
    current = root
    for component in (None, *target.relative_to(root).parts):
        if component is not None:
            current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            info = current.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("certificate_directory_not_private")


def write_private(path, data):
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def prepare(directory: Path):
    private_directory_tree(directory.parent, directory)
    required = ("ca.crt", "ca.key", "wrong-ca.crt", "server.crt", "server.key")
    if any((directory / name).exists() for name in required):
        if not all(
            (directory / name).is_file()
            and not (directory / name).is_symlink()
            and (directory / name).stat().st_mode & 0o777 == 0o600
            and (directory / name).stat().st_uid == os.getuid()
            for name in required
        ):
            raise ValueError("certificate_inventory_invalid")
        ca = x509.load_pem_x509_certificate((directory / "ca.crt").read_bytes())
        server = x509.load_pem_x509_certificate((directory / "server.crt").read_bytes())
        server.verify_directly_issued_by(ca)
        if min(ca.not_valid_after_utc, server.not_valid_after_utc) <= datetime.now(
            UTC
        ) + timedelta(minutes=1):
            raise ValueError("certificate_renewal_required")
        expired(directory)
        return
    now = datetime.now(UTC)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Guardrails evolution local CA")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_ca = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(wrong.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(wrong.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(wrong.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(wrong, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    names = [
        "probe.gr-platform.svc.cluster.local",
        "probe.gr-platform.svc",
        "probe",
        "localhost",
    ]
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
        .issuer_name(subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    *(x509.DNSName(name) for name in names),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(True, False, True, False, False, False, False, False, False),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    for name, cert in (
        ("ca.crt", ca),
        ("wrong-ca.crt", wrong_ca),
        ("server.crt", server),
    ):
        write_private(directory / name, cert.public_bytes(serialization.Encoding.PEM))
    for name, secret in (("ca.key", key), ("server.key", server_key)):
        write_private(
            directory / name,
            secret.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )

    expired(directory)


def expired(directory):
    path = directory / "expired.crt"
    if path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o777 != 0o600:
            raise ValueError("expired_fixture_file_invalid")
        return
    ca = x509.load_pem_x509_certificate((directory / "ca.crt").read_bytes())
    server = x509.load_pem_x509_certificate((directory / "server.crt").read_bytes())
    key = serialization.load_pem_private_key(
        (directory / "ca.key").read_bytes(), password=None
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(server.subject)
        .issuer_name(ca.subject)
        .public_key(server.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now - timedelta(days=1))
        .add_extension(
            server.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value,
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(True, False, True, False, False, False, False, False, False),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    write_private(path, cert.public_bytes(serialization.Encoding.PEM))


def issue_server(directory: Path, target: Path, *, names: list[str]):
    """Issue a component-local key; reuse only a valid certificate for exact names."""
    private_directory_tree(directory.parent, target)
    prepare(directory)
    ca = x509.load_pem_x509_certificate((directory / "ca.crt").read_bytes())
    signing = serialization.load_pem_private_key(
        (directory / "ca.key").read_bytes(), password=None
    )
    expected_names = sorted({*names, "localhost", "127.0.0.1"})
    sans = [
        x509.IPAddress(ipaddress.ip_address(value))
        if value == "127.0.0.1"
        else x509.DNSName(value)
        for value in expected_names
    ]
    paths = [target / "tls.crt", target / "tls.key", target / "ca.crt"]
    if any(path.exists() for path in paths):
        if not all(
            path.is_file()
            and not path.is_symlink()
            and path.stat().st_uid == os.getuid()
            and path.stat().st_mode & 0o777 == 0o600
            for path in paths
        ):
            raise ValueError("component_certificate_inventory_invalid")
        cert = x509.load_pem_x509_certificate(paths[0].read_bytes())
        key = serialization.load_pem_private_key(paths[1].read_bytes(), password=None)
        cert.verify_directly_issued_by(ca)
        if cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ) != key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ):
            raise ValueError("component_certificate_key_mismatch")
        if (
            set(
                cert.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value
            )
            != set(sans)
            or cert.not_valid_after_utc <= datetime.now(UTC) + timedelta(minutes=1)
            or paths[2].read_bytes() != (directory / "ca.crt").read_bytes()
        ):
            raise ValueError("component_certificate_renewal_required")
        return
    now = datetime.now(UTC)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, True, False, False, False, False, False, False),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(signing, hashes.SHA256())
    )
    write_private(paths[0], cert.public_bytes(serialization.Encoding.PEM))
    write_private(
        paths[1],
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    write_private(paths[2], (directory / "ca.crt").read_bytes())
