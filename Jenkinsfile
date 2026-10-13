// Phronesis-Forschungscampus – CI/CD-Pipeline
//
// Vorgesehene Jobs (Pipeline script from SCM, je ein Job pro Branch):
//   university develop      → Prüfung + Tests, kein Deploy
//   university test         → Prüfung + Tests + Deploy test
//   university integration  → Prüfung + Tests + Deploy int
//   university main         → Prüfung + Tests + Deploy prod
//
// Tests laufen gegen ein echtes PostgreSQL 16 (Sidecar-Container im Build-Netz), kein SQLite-Ersatz
// (Paket-CLAUDE.md, docs/S0_VORSCHLAG.md). Die Stage braucht nur die Docker-Kommandozeile im Jenkins-Container,
// kein Docker-Pipeline-Plugin. Der Workspace liegt im Jenkins-Volume; der Testcontainer bindet dasselbe Volume ein
// (JENKINS_HOME_VOLUME, Standard: university_jenkins_data aus docker-compose.jenkins.yml).
//
// Die Deploy-Stages sind absichtlich NICHT aktiviert: Zielhost und Betriebsform sind noch zu entscheiden
// (Architektur Kap. 24, S0-Vorschlag Lücke L2).

pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    environment {
        REPO_URL = 'https://github.com/kaspAir/university'
        JENKINS_HOME_VOLUME = 'university_jenkins_data'
    }

    stages {

        stage('Prüfung Repository') {
            steps {
                sh '''
                    set -e
                    echo "Commit: $(git rev-parse --short HEAD)"
                    test -f Jenkinsfile
                    test -f CLAUDE.md
                    test -f ci/run_tests.sh
                    test -f 00_Basisdokumente/Phronesis_Forschungsverfassung_V0.2.docx
                    test -f 00_Basisdokumente/Phronesis_Campus_Architektur_V0.1.md
                    test -f 00_Basisdokumente/Phronesis_Campus_V0.1/MANIFEST.sha256.json
                    echo "OK: Grundlagen vorhanden"
                '''
            }
        }

        stage('Tests gegen PostgreSQL') {
            steps {
                sh '''
                    set -eu
                    NET="campus-ci-$(printf '%s' "$BUILD_TAG" | tr -c 'A-Za-z0-9_.-' '-' | tr 'A-Z' 'a-z')"
                    PG="pg-$NET"
                    TP="tp-$NET"
                    cleanup() {
                        docker rm -f "$PG" "$TP" >/dev/null 2>&1 || true
                        docker network rm "$NET" >/dev/null 2>&1 || true
                    }
                    trap cleanup EXIT
                    cleanup
                    docker network create "$NET"
                    docker run -d --name "$PG" --network "$NET" \
                        -e POSTGRES_USER=campus_owner -e POSTGRES_PASSWORD=ci -e POSTGRES_DB=campus_test \
                        postgres:16
                    # Temporal dev server as a second sidecar (S2 workflow tests); in-memory, per build
                    docker run -d --name "$TP" --network "$NET" temporalio/temporal:latest server start-dev --ip 0.0.0.0
                    docker run --rm --network "$NET" \
                        -u "$(id -u):$(id -g)" -e HOME=/tmp \
                        -v "$JENKINS_HOME_VOLUME:/var/jenkins_home" -w "$WORKSPACE" \
                        -e CAMPUS_TEST_DATABASE_URL="postgresql+psycopg://campus_owner:ci@$PG:5432/campus_test" \
                        -e CAMPUS_TEST_TEMPORAL_ADDRESS="$TP:7233" \
                        -e CAMPUS_CAPABILITY_LEVEL=development_mock -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
                        python:3.12-slim sh ci/run_tests.sh
                '''
            }
            post {
                always {
                    junit allowEmptyResults: false, testResults: 'reports/junit.xml'
                }
            }
        }

        stage('Deploy') {
            // Userland pilot on the shared Infomaniak host (ADR-0015): only the branch main deploys, from any job that builds it.
            // deploy.sh: backup -> checkout -> deps -> migrations -> restart -> health with the new build sha; code rollback on failure.
            // The key comes from the credential 'university-deploy' via credentials-binding (masked in logs); no secret in the repo.
            when {
                anyOf {
                    branch 'main'
                    expression { (env.GIT_BRANCH ?: '').endsWith('main') }
                }
            }
            steps {
                withCredentials([sshUserPrivateKey(credentialsId: 'university-deploy', keyFileVariable: 'DEPLOY_KEY', usernameVariable: 'DEPLOY_USER')]) {
                    sh '''
                        set -eu
                        ssh -i "$DEPLOY_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o BatchMode=yes                             "$DEPLOY_USER@83.228.238.194" "bash ~/campus/ops/server/deploy.sh ${GIT_COMMIT}"
                        # success criterion over the public HTTPS entry point: the deployed build answers
                        curl -fsS --max-time 20 https://university.kaspair.ch/api/v1/health | grep -qF "${GIT_COMMIT}"
                        curl -sS --max-time 20 https://university.kaspair.ch/api/v1/ready || true
                    '''
                }
            }
        }
    }
}
