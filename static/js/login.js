// Login page JavaScript
document.addEventListener('DOMContentLoaded', function() {
    const loginForm = document.getElementById('loginForm');
    const errorMessage = document.getElementById('errorMessage');
    const loginText = document.getElementById('loginText');
    const loginLoader = document.getElementById('loginLoader');
    
    // Handle bfcache (back/forward cache)
    window.addEventListener('pageshow', function(event) {
        if (event.persisted) {
            window.location.reload();
        }
    });
    
    loginForm.addEventListener('submit', async function(e) {
        e.preventDefault();
        
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;
        
        // Show loader
        loginText.style.display = 'none';
        loginLoader.style.display = 'inline-block';
        errorMessage.style.display = 'none';
        
        try {
            const response = await fetch('/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ username, password })
            });
            
            const data = await response.json();
            
            if (data.success) {
                window.location.href = '/dashboard';
            } else {
                // Show error
                errorMessage.textContent = data.error || 'Invalid credentials';
                errorMessage.style.display = 'block';
                
                // Reset loader
                loginText.style.display = 'inline';
                loginLoader.style.display = 'none';
            }
        } catch (error) {
            errorMessage.textContent = 'Connection error. Please try again.';
            errorMessage.style.display = 'block';
            
            // Reset loader
            loginText.style.display = 'inline';
            loginLoader.style.display = 'none';
        }
    });
});
