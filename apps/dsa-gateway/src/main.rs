use dsa_gateway::{serve, Config};

#[tokio::main]
async fn main() {
    let config = match Config::from_env() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[dsa-gateway] invalid configuration: {e}");
            std::process::exit(2);
        }
    };
    let addr = config.bind_addr();
    let listener = match tokio::net::TcpListener::bind(&addr).await {
        Ok(l) => l,
        Err(e) => {
            eprintln!("[dsa-gateway] failed to bind {addr}: {e}");
            std::process::exit(1);
        }
    };
    let bound = listener
        .local_addr()
        .map(|a| a.to_string())
        .unwrap_or(addr);
    println!(
        "[dsa-gateway] listening on http://{bound} -> backend {}",
        config.backend_url
    );
    if let Err(e) = serve(listener, config).await {
        eprintln!("[dsa-gateway] server error: {e}");
        std::process::exit(1);
    }
}
