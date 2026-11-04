//! The device's WireGuard key. It's made on this PC and the server only ever sees the public half.

use base64::{engine::general_purpose::STANDARD, Engine};
use x25519_dalek::{PublicKey, StaticSecret};
use zeroize::Zeroizing;

pub struct KeyPair {
    pub private_b64: Zeroizing<String>,
    pub public_b64: String,
}

fn from_bytes(mut raw: [u8; 32]) -> KeyPair {
    raw[0] &= 248; // the clamping every WireGuard key has
    raw[31] &= 127;
    raw[31] |= 64;
    let secret = StaticSecret::from(raw);
    let public = PublicKey::from(&secret);
    let private_b64 = Zeroizing::new(STANDARD.encode(raw));
    zeroize::Zeroize::zeroize(&mut raw);
    KeyPair { private_b64, public_b64: STANDARD.encode(public.as_bytes()) }
}

pub fn generate() -> Result<KeyPair, String> {
    let mut raw = [0u8; 32];
    getrandom::getrandom(&mut raw).map_err(|e| format!("Couldn't make a device key: {e}"))?;
    Ok(from_bytes(raw))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_the_published_x25519_test_vector() {
        // RFC 7748, section 6.1: Alice's keys
        let mut private = [0u8; 32];
        for (i, b) in "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a".as_bytes().chunks(2).enumerate() {
            private[i] = u8::from_str_radix(std::str::from_utf8(b).unwrap(), 16).unwrap();
        }
        let pair = from_bytes(private);
        let public = STANDARD.decode(&pair.public_b64).unwrap();
        let hex: String = public.iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(hex, "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a");
    }

    #[test]
    fn makes_valid_different_keys() {
        let (a, b) = (generate().unwrap(), generate().unwrap());
        assert_ne!(*a.private_b64, *b.private_b64);
        assert_ne!(a.public_b64, b.public_b64);
        for key in [a.private_b64.as_str(), a.public_b64.as_str()] {
            assert_eq!(key.len(), 44);
            assert_eq!(STANDARD.decode(key).unwrap().len(), 32);
        }
    }
}
